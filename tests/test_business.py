"""Regras de negócio: hold, conflito, confirmação e integridade de estado."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.business
class TestHold:
    def test_hold_de_assentos_disponiveis_retorna_201_e_token(self, client: TestClient, hold) -> None:
        token, res = hold([1, 2, 3], "alice")
        assert token and len(token) == 32
        assert res.json()["seats"] == [1, 2, 3]
        assert res.json()["expires_in_seconds"] > 0

    def test_hold_defini_duplicatas_e_responde_sorted(self, client: TestClient, hold) -> None:
        token, res = hold([3, 1, 2, 1], "alice")
        assert res.json()["seats"] == [1, 2, 3]

    def test_conflito_parcial_nao_aplica_half_state(self, client: TestClient, hold) -> None:
        hold([3], "alice")
        res = client.post("/holds", json={"seat_numbers": [3, 4], "user_id": "bob"})
        assert res.status_code == 409
        # Assento livre do pedido conflitante permanece disponível (all-or-nothing)
        seat4 = next(s for s in client.get("/seats").json() if s["number"] == 4)
        assert seat4["status"] == "available"

    def test_hold_fora_do_intervalo_1_a_20_rejeita(self, client: TestClient) -> None:
        for out_of_range in ([0], [21], [-1]):
            res = client.post("/holds", json={"seat_numbers": out_of_range, "user_id": "x"})
            assert res.status_code == 400

    def test_hold_sem_assentos_rejeita(self, client: TestClient) -> None:
        res = client.post("/holds", json={"seat_numbers": [], "user_id": "x"})
        assert res.status_code == 422

    def test_hold_de_todos_os_20_assentos_aceito(self, client: TestClient, hold) -> None:
        token, res = hold(list(range(1, 21)), "alice")
        assert res.json()["seats"] == list(range(1, 21))


@pytest.mark.business
class TestConfirm:
    def test_confirmacao_converte_hold_em_confirmed(self, client: TestClient, hold) -> None:
        token, _ = hold([1, 2], "alice")
        res = client.post("/confirms", json={"token": token, "user_id": "alice"})
        assert res.status_code == 200
        assert res.json()["seats"] == [1, 2]
        statuses = {s["number"]: s["status"] for s in client.get("/seats").json()}
        assert statuses[1] == statuses[2] == "confirmed"

    def test_reconfirmacao_do_mesmo_token_falha_404(self, client: TestClient, hold) -> None:
        token, _ = hold([5], "carol")
        client.post("/confirms", json={"token": token, "user_id": "carol"})
        res = client.post("/confirms", json={"token": token, "user_id": "carol"})
        assert res.status_code == 404

    def test_confirmacao_por_outro_usuario_falha_404(self, client: TestClient, hold) -> None:
        token, _ = hold([5], "carol")
        res = client.post("/confirms", json={"token": token, "user_id": "bob"})
        assert res.status_code == 404

    def test_token_aleatorio_falha_404(self, client: TestClient) -> None:
        res = client.post("/confirms", json={"token": "0" * 32, "user_id": "anyone"})
        assert res.status_code == 404

    def test_malformed_token_rejeitado_422(self, client: TestClient) -> None:
        res = client.post("/confirms", json={"token": "curto", "user_id": "x"})
        assert res.status_code == 422

    def test_assento_confirmado_nao_pode_ser_hold_novamente(self, client: TestClient, hold) -> None:
        token, _ = hold([9], "alice")
        client.post("/confirms", json={"token": token, "user_id": "alice"})
        res = client.post("/holds", json={"seat_numbers": [9], "user_id": "bob"})
        assert res.status_code == 409


@pytest.mark.business
class TestSeats:
    def test_listagem_inicial_20_disponiveis(self, client: TestClient) -> None:
        seats = client.get("/seats").json()
        assert len(seats) == 20
        assert all(s["status"] == "available" for s in seats)

    def test_listagem_mostra_held_by_e_ttl_para_held(self, client: TestClient, hold) -> None:
        hold([6], "alice")
        seat = next(s for s in client.get("/seats").json() if s["number"] == 6)
        assert seat["status"] == "held"
        assert seat["held_by"] == "alice"
        assert 0 < seat["expires_in_seconds"] <= 60

    def test_ordem_crescente_na_listagem(self, client: TestClient) -> None:
        numbers = [s["number"] for s in client.get("/seats").json()]
        assert numbers == sorted(numbers)