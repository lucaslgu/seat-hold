# Seat Hold Service

Mini serviço HTTP de reserva de assentos numerados de **1 a 20**. O usuário pode
**segurar (hold)** assentos por **60 segundos** e depois **confirmá-los**. Assentos
não confirmados expiram automaticamente e voltam a ficar disponíveis.

FastAPI + Redis local. Estado compartilhado: qualquer instância do serviço fala
com o mesmo Redis — pronto para escalar horizontalmente.

## Arquitetura

| Chave Redis        | Conteúdo                 | TTL   | Papel                          |
|--------------------|--------------------------|-------|--------------------------------|
| `seat:{n}:hold`    | token do hold            | 60s   | existe = held; expira = livre  |
| `token:{token}`    | hash `{user, seats}`     | 60s   | metadados do hold              |
| `seats:confirmed`  | hash `assento → user`    | —     | confirmações permanentes       |

- **Expiração nativa**: TTL do Redis libera o assento; nenhum sweep/scan em background.
- **Atomicidade**: hold e confirm são scripts Lua — executados serialmente pelo Redis (all-or-nothing).
- **Complexidade**: O(1) round-trip por operação (1 `EVAL`), leitura em pipeline — nunca O(n) no total de assentos.

## Como rodar

```bash
# pré-requisito: Redis local na porta padrão 6379
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn redis

# subir (opcional --reload p/ dev)
.venv/bin/uvicorn app:app --port 8000
```

- API: <http://localhost:8000>
- **Swagger UI**: <http://localhost:8000/docs>
- OpenAPI JSON: <http://localhost:8000/openapi.json>

## Endpoints

| Método | Caminho               | Sucesso | Erros                        |
|--------|-----------------------|---------|------------------------------|
| GET    | `/seats`              | 200     | —                            |
| POST   | `/holds`              | 201     | 400 fora do range, 409 conflito, 422 body |
| POST   | `/confirms`           | 200     | 404 token inválido/expirado, 422 body |
| DELETE | `/confirms`           | 200     | 400 fora do range, 404 não confirmado/outra pessoa |
| GET    | `/users/{id}/seats`   | 200     | —                            |

## Fluxo completo (curl)

```bash
# 0) Estado inicial: os 20 assentos disponíveis
curl -s localhost:8000/seats | python3 -m json.tool

# 1) Segurar os assentos 1, 2 e 3 para alice (201 + token, hold de 60s)
T=$(curl -s -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' \
  -d '{"seat_numbers": [1, 2, 3], "user_id": "alice"}')
echo "$T"
TOKEN=$(echo "$T" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 2) Listar: 1-3 aparecem como "held" por alice com countdown
curl -s localhost:8000/seats | python3 -c \
  "import sys,json;print([s for s in json.load(sys.stdin) if s['status']!='available'])"

# 3) Conflito: bob tenta segurar o 3 (já held) → 409, e o 4 permanece livre
curl -s -i -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' \
  -d '{"seat_numbers": [3, 4], "user_id": "bob"}' | head -1
curl -s localhost:8000/seats | python3 -c \
  "import sys,json;print(next(s for s in json.load(sys.stdin) if s['number']==4)['status'])"  # available

# 4) Fora do intervalo 1-20 → 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' \
  -d '{"seat_numbers": [21], "user_id": "bob"}'

# 5) Confirmar com usuário errado → 404
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/confirms \
  -H 'Content-Type: application/json' \
  -d "{\"token\": \"$TOKEN\", \"user_id\": \"bob\"}"

# 6) Confirmar com o dono → 200 (assentos vão para confirmed)
curl -s -X POST localhost:8000/confirms \
  -H 'Content-Type: application/json' \
  -d "{\"token\": \"$TOKEN\", \"user_id\": \"alice\"}"

# 7) Reconfirmar o mesmo token → 404 (consumido)
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/confirms \
  -H 'Content-Type: application/json' \
  -d "{\"token\": \"$TOKEN\", \"user_id\": \"alice\"}"

# 8) Assento confirmado não pode ser hold → 409
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' \
  -d '{"seat_numbers": [1], "user_id": "bob"}'

# 9) Timeout: segurar o 20, expirar (TTL=1s p/ demo) e ver disponível de novo
T2=$(curl -s -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' -d '{"seat_numbers": [20], "user_id": "carol"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
redis-cli expire "seat:20:hold" 1 && redis-cli expire "token:$T2" 1 && sleep 1.1
curl -s localhost:8000/seats | python3 -c \
  "import sys,json;print(next(s for s in json.load(sys.stdin) if s['number']==20)['status'])"  # available

# 10) Assentos do usuário: confirmando o 4 e vendo os de alice
T3=$(curl -s -X POST localhost:8000/holds \
  -H 'Content-Type: application/json' -d '{"seat_numbers": [4], "user_id": "alice"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -X POST localhost:8000/confirms -H 'Content-Type: application/json' \
  -d "{\"token\": \"$T3\", \"user_id\": \"alice\"}" > /dev/null
curl -s localhost:8000/users/alice/seats

# 11) Cancelar confirmado: alice libera o 4 e ele volta a available
curl -s -X DELETE localhost:8000/confirms \
  -H 'Content-Type: application/json' -d '{"seat_numbers": [4], "user_id": "alice"}'
curl -s localhost:8000/seats | python3 -c \
  "import sys,json;print(next(s for s in json.load(sys.stdin) if s['number']==4)['status'])"  # available

# 12) Cancelar assento confirmado de outra pessoa → 404
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8000/confirms \
  -H 'Content-Type: application/json' -d '{"seat_numbers": [1], "user_id": "bob"}'
```

## Testes

```bash
.venv/bin/python -m pytest tests/ -q          # suíte completa (45 testes)
.venv/bin/python -m pytest -m business -q     # só regras de negócio
.venv/bin/python -m pytest -m timeout_ttl -q  # timeouts/TTL
.venv/bin/python -m pytest -m performance -q  # budget de latência
.venv/bin/python -m pytest -m algorithmic -q  # complexidade/round-trips
.venv/bin/python -m pytest -m integration -q  # integração Redis
.venv/bin/python -m pytest -m standards -q    # padrões/OpenAPI
```

Cobertura: regras de negócio, expiração real, latência (p99), O(1) round-trips
provado por spy, concorrência (exatamente 1 vencedor entre N disputantes),
queda do Redis e contratos HTTP/OpenAPI.

## Licença

[MIT](LICENSE)