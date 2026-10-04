"""Regras de negócio das funcionalidades extras: cancelamento e assentos por usuário."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.business
class TestCancel:
    def test_cancelar_confirmado_libera_assento(self, client: TestClient, hold) -> None:
        token, _ = hold([3], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [3], "user_id": "alice"})
        assert res.status_code == 200
        seat = next(s for s in client.get("/seats").json() if s["number"] == 3)
        assert seat["status"] == "available"

    def test_cancelar_confirmado_permite_novo_hold(self, client: TestClient, hold) -> None:
        token, _ = hold([3], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        client.request("DELETE", "/confirms", json={"seat_numbers": [3], "user_id": "alice"})
        res = client.post("/holds", json={"seat_numbers": [3], "user_id": "bob"})
        assert res.status_code == 201

    def test_cancelar_assento_de_outro_usuario_404(self, client: TestClient, hold) -> None:
        token, _ = hold([4], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [4], "user_id": "bob"})
        assert res.status_code == 404

    def test_cancelar_assento_nao_confirmado_404(self, client: TestClient, hold) -> None:
        hold([5], "alice")  # held, não confirmado
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [5], "user_id": "alice"})
        assert res.status_code == 404

    def test_cancelar_assento_disponivel_404(self, client: TestClient) -> None:
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [7], "user_id": "alice"})
        assert res.status_code == 404

    def test_cancelar_fora_do_range_400(self, client: TestClient) -> None:
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [21], "user_id": "alice"})
        assert res.status_code == 400

    def test_cancelar_varios_de_uma_vez(self, client: TestClient, hold) -> None:
        token, _ = hold([1, 2, 3], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        res = client.request("DELETE", "/confirms", json={"seat_numbers": [1, 3], "user_id": "alice"})
        assert res.status_code == 200
        statuses = {s["number"]: s["status"] for s in client.get("/seats").json()}
        assert statuses[1] == statuses[3] == "available"
        assert statuses[2] == "confirmed"


@pytest.mark.business
class TestUserSeats:
    def test_lista_confirmados_e_helds_do_usuario(self, client: TestClient, hold) -> None:
        token, _ = hold([1, 2], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        hold([5], "alice")
        res = client.get("/users/alice/seats")
        assert res.status_code == 200
        data = res.json()
        assert data["confirmed"] == [1, 2]
        assert data["held"] == [5]

    def test_usuario_sem_assentos_retorna_listas_vazias(self, client: TestClient) -> None:
        res = client.get("/users/nobody/seats")
        assert res.status_code == 200
        assert res.json() == {"user_id": "nobody", "confirmed": [], "held": [], "held_seconds_left": None}

    def test_nao_expoe_assentos_de_outro_usuario(self, client: TestClient, hold) -> None:
        hold([8], "alice")
        client.post("/holds", json={"seat_numbers": [9], "user_id": "bob"})
        data = client.get("/users/bob/seats").json()
        assert data["held"] == [9]
        assert 8 not in data["held"] and 8 not in data["confirmed"]

    def test_held_inclui_tempo_restante(self, client: TestClient, hold) -> None:
        hold([5], "alice")
        data = client.get("/users/alice/seats").json()
        assert 0 < data["held_seconds_left"] <= 60