"""Integrações: Redis, conexão, persistência entre requisições e recuperação."""

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

import app as app_module
from app import r


@pytest.mark.integration
class TestRedisIntegration:
    def test_redis_local_disponivel(self) -> None:
        assert r.ping()

    def test_estado_persiste_entre_requisicoes_no_mesmo_app(self, client: TestClient, hold) -> None:
        token, _ = hold([4], "alice")
        client2 = TestClient(app_module.app)
        # estado visível por outra "instância" de app (mesmo Redis)
        seats = client2.get("/seats").json()
        assert next(s for s in seats if s["number"] == 4)["status"] == "held"
        # e a confirmação funciona vinda da outra instância
        res = client2.post("/confirms", json={"token": token, "user_id": "alice"})
        assert res.status_code == 200

    def test_chaves_usam_o_esquema_documentado(self, client: TestClient, hold) -> None:
        token, _ = hold([8], "alice")
        assert r.get("seat:8:hold") == token
        assert r.hget(f"token:{token}", "user") == "alice"
        assert r.hget(f"token:{token}", "seats") == "8"

    def test_multiplos_holds_independentes_coexistem(self, client: TestClient, hold) -> None:
        for s in range(1, 6):
            hold([s], f"u{s}")
        held = [s["number"] for s in client.get("/seats").json() if s["status"] == "held"]
        assert held == [1, 2, 3, 4, 5]

    def test_redis_indisponivel_resulta_em_erro_servidor(self, hold, monkeypatch) -> None:
        # Simula queda do Redis no meio da operação
        class Down:
            def eval(self, *a, **kw):
                raise redis_lib.ConnectionError("connection refused")

        monkeypatch.setattr(app_module, "r", Down())
        client = TestClient(app_module.app, raise_server_exceptions=False)
        res = client.post("/holds", json={"seat_numbers": [2], "user_id": "x"})
        assert res.status_code == 500

    def test_recuperacao_apos_falha_transitoria(self, client, hold, monkeypatch) -> None:
        # falha só no primeiro eval, depois volta
        orig = r.eval
        state = {"down": True}

        def flaky(*a, **kw):
            if state["down"]:
                state["down"] = False
                raise redis_lib.ConnectionError("transient")
            return orig(*a, **kw)

        monkeypatch.setattr(r, "eval", flaky)
        client = TestClient(app_module.app, raise_server_exceptions=False)
        res1 = client.post("/holds", json={"seat_numbers": [2], "user_id": "y"})
        assert res1.status_code == 500
        res2 = client.post("/holds", json={"seat_numbers": [2], "user_id": "y"})
        assert res2.status_code == 201
        monkeypatch.undo()


@pytest.mark.integration
class TestContract:
    """Contratos HTTP: content-type, campos obrigatórios, JSON válido."""

    def test_endpoints_exigem_json_valido(self, client: TestClient) -> None:
        res = client.post("/holds", data="not json", headers={"Content-Type": "application/json"})
        assert res.status_code == 422

    def test_hold_sem_user_id_rejeita_422(self, client: TestClient) -> None:
        res = client.post("/holds", json={"seat_numbers": [1]})
        assert res.status_code == 422

    def test_confirm_sem_user_id_rejeita_422(self, client: TestClient) -> None:
        res = client.post("/confirms", json={"token": "a" * 32})
        assert res.status_code == 422

    def test_user_id_vazio_rejeita_422(self, client: TestClient) -> None:
        res = client.post("/holds", json={"seat_numbers": [1], "user_id": ""})
        assert res.status_code == 422

    def test_tipos_errados_rejeita_422(self, client: TestClient) -> None:
        res = client.post("/holds", json={"seat_numbers": "1", "user_id": "x"})
        assert res.status_code == 422