"""Mini serviço de reserva de assentos (1-20) com hold de 60s e confirmação.

Armazenamento 100% Redis:
- seat:{n}:hold  -> token do hold, TTL = HOLD_SECONDS (expiração nativa)
- seats:confirmed -> hash assento -> user (sem TTL)
- token:{token}   -> metadados do hold (user, seats), TTL = HOLD_SECONDS

Atomicidade garantida por scripts Lua (Redis executa scripts serialmente).
"""

import uuid
from typing import Annotated, Any

import redis
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from pydantic import field_validator

HOLD_SECONDS = 60
SEAT_RANGE = range(1, 21)

r = redis.Redis(decode_responses=True)

# KEYS[1]: seats:confirmed + KEYS[2..]: chaves seat:{n}:hold
# ARGV: 1=ttl, 2=token, 3=user, 4..=números dos assentos
_HOLD_LUA = """
local confirmed = KEYS[1]
local ttl = tonumber(ARGV[1])
local token = ARGV[2]
local user = ARGV[3]
local conflicts = {}
for i = 2, #KEYS do
    local seat = ARGV[2 + i]
    if redis.call('EXISTS', KEYS[i]) == 1
       or redis.call('HEXISTS', confirmed, seat) == 1 then
        conflicts[#conflicts + 1] = seat
    end
end
if #conflicts > 0 then return conflicts end
local seats = {}
for i = 2, #KEYS do
    redis.call('SET', KEYS[i], token, 'EX', ttl)
    seats[#seats + 1] = ARGV[2 + i]
end
redis.call('HSET', 'token:' .. token, 'user', user, 'seats', table.concat(seats, ','))
redis.call('EXPIRE', 'token:' .. token, ttl)
return {}
"""

# KEYS: seats:confirmed | ARGV: token, user
_CONFIRM_LUA = """
local token = ARGV[1]
local user = ARGV[2]
local meta = redis.call('HGETALL', 'token:' .. token)
if #meta == 0 then return false end
local data = {}
for i = 1, #meta, 2 do data[meta[i]] = meta[i + 1] end
if data['user'] ~= user then return false end
local seats = {}
for seat in string.gmatch(data['seats'], '[^,]+') do
    seats[#seats + 1] = seat
    if redis.call('HEXISTS', KEYS[1], seat) == 1 then
        return false
    end
end
for _, seat in ipairs(seats) do
    local hold_key = 'seat:' .. seat .. ':hold'
    if redis.call('GET', hold_key) ~= token then
        return false
    end
end
for _, seat in ipairs(seats) do
    redis.call('HSET', KEYS[1], seat, user)
    redis.call('DEL', 'seat:' .. seat .. ':hold')
end
redis.call('DEL', 'token:' .. token)
return seats
"""


class SeatOut(BaseModel):
    number: int
    status: str
    held_by: str | None = None
    expires_in_seconds: float | None = None


class HoldRequest(BaseModel):
    seat_numbers: Annotated[list[int], Field(min_length=1)]
    user_id: Annotated[str, Field(min_length=1)]

    @field_validator("seat_numbers", mode="after")
    @classmethod
    def dedupe(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class ConfirmRequest(BaseModel):
    token: Annotated[str, Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")]
    user_id: str


class CancelRequest(BaseModel):
    seat_numbers: Annotated[list[int], Field(min_length=1)]
    user_id: Annotated[str, Field(min_length=1)]

    @field_validator("seat_numbers", mode="after")
    @classmethod
    def dedupe(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class UserSeatsOut(BaseModel):
    user_id: str
    confirmed: list[int]
    held: list[int]
    held_seconds_left: float | None = None


class HoldOut(BaseModel):
    token: str
    user_id: str
    seats: list[int]
    expires_in_seconds: float


app = FastAPI(
    title="Seat Hold Service",
    description="Reserva de assentos (1-20) com hold de 60s e confirmação.",
    version="1.0.0",
)


@app.get("/seats", response_model=list[SeatOut])
def get_seats() -> Any:
    n_seats = len(SEAT_RANGE)
    pipe = r.pipeline()
    for n in SEAT_RANGE:
        pipe.get(f"seat:{n}:hold")
        pipe.ttl(f"seat:{n}:hold")
    hold_data = pipe.execute()
    confirmed = r.hgetall("seats:confirmed")

    tokens = {t for t in hold_data[::2] if t}
    users: dict[str, str | None] = {}
    if tokens:
        pipe = r.pipeline()
        for t in tokens:
            pipe.hget(f"token:{t}", "user")
        users = dict(zip(tokens, pipe.execute()))

    seats = []
    for i in range(n_seats):
        token, ttl = hold_data[i * 2], hold_data[i * 2 + 1]
        n = SEAT_RANGE[i]
        if token:
            seats.append(SeatOut(number=n, status="held", held_by=users.get(token), expires_in_seconds=ttl))
        elif str(n) in confirmed:
            seats.append(SeatOut(number=n, status="confirmed", held_by=confirmed[str(n)]))
        else:
            seats.append(SeatOut(number=n, status="available"))
    return seats


@app.post("/holds", status_code=status.HTTP_201_CREATED, response_model=HoldOut)
def hold_seats(request: HoldRequest) -> Any:
    numbers = set(request.seat_numbers)
    invalid = sorted(numbers - set(SEAT_RANGE))
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Assentos fora do intervalo 1-20: {invalid}",
        )

    token = uuid.uuid4().hex
    keys = ["seats:confirmed", *(f"seat:{n}:hold" for n in numbers)]
    conflicts = r.eval(
        _HOLD_LUA, len(keys), *keys, HOLD_SECONDS, token, request.user_id, *map(str, sorted(numbers))
    )
    if conflicts:
        raise HTTPException(status.HTTP_409_CONFLICT, {"detail": list(conflicts)})

    return HoldOut(
        token=token,
        user_id=request.user_id,
        seats=sorted(numbers),
        expires_in_seconds=HOLD_SECONDS,
    )


@app.post("/confirms", response_model=HoldOut)
def confirm_holds(request: ConfirmRequest) -> Any:
    result = r.eval(_CONFIRM_LUA, 1, "seats:confirmed", request.token, request.user_id)
    # Lua `return false` chega como None; Lua `return {...}` chega como list
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Token inválido, expirado ou pertencente a outro usuário.",
        )
    return HoldOut(
        token=request.token,
        user_id=request.user_id,
        seats=[int(s) for s in result],
        expires_in_seconds=0,
    )


# KEYS[1]: seats:confirmed | ARGV: 1=user, 2..=assentos (somados ao hash sem TTL)
_CANCEL_LUA = """
local user = ARGV[1]
local cancelled = {}
for i = 2, #ARGV do
    local seat = ARGV[i]
    if redis.call('HGET', KEYS[1], seat) == user then
        redis.call('HDEL', KEYS[1], seat)
        cancelled[#cancelled + 1] = seat
    else
        return false
    end
end
return cancelled
"""


@app.post("/cancels", response_model=UserSeatsOut)
def cancel_confirmed(request: CancelRequest) -> Any:
    numbers = set(request.seat_numbers)
    invalid = sorted(numbers - set(SEAT_RANGE))
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Assentos fora do intervalo 1-20: {invalid}",
        )

    args = [request.user_id, *map(str, sorted(numbers))]
    result = r.eval(_CANCEL_LUA, 1, "seats:confirmed", *args)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Nenhum assento confirmado para este usuário nos informados.",
        )
    seats = r.hkeys("seats:confirmed") if r.hlen("seats:confirmed") else []
    return UserSeatsOut(
        user_id=request.user_id,
        confirmed=sorted(int(s) for s in seats),
        held=[],
        held_seconds_left=None,
    )


@app.get("/users/{user_id}/seats", response_model=UserSeatsOut)
def get_user_seats(user_id: str) -> Any:
    confirmed_hash = r.hgetall("seats:confirmed")
    confirmed = sorted(int(s) for s, u in confirmed_hash.items() if u == user_id)

    pipe = r.pipeline()
    for n in SEAT_RANGE:
        pipe.get(f"seat:{n}:hold")
        pipe.ttl(f"seat:{n}:hold")
    hold_data = pipe.execute()

    held, min_ttl = [], None
    tokens = {t for t in hold_data[::2] if t}
    if tokens:
        users = {
            t: r.hget(f"token:{t}", "user")
            for t in tokens
        }
        for i, t in enumerate(hold_data[::2]):
            if t and users.get(t) == user_id:
                held.append(SEAT_RANGE[i])
                ttl = hold_data[i * 2 + 1]
                min_ttl = ttl if min_ttl is None else min(min_ttl, ttl)

    return UserSeatsOut(
        user_id=user_id,
        confirmed=confirmed,
        held=held,
        held_seconds_left=min_ttl if held else None,
    )


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()