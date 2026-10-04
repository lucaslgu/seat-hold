"""Conftest: limpeza do Redis entre testes + client injetado."""

import pytest
from fastapi.testclient import TestClient

from app import app, r


@pytest.fixture(autouse=True)
def clean_redis() -> None:
    r.flushdb()
    yield
    r.flushdb()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def hold(client: TestClient):
    """Factory: cria hold e retorna (token, resposta)."""

    def _hold(seats: list[int], user: str):
        res = client.post("/holds", json={"seat_numbers": seats, "user_id": user})
        assert res.status_code == 201, res.text
        return res.json()["token"], res

    return _hold