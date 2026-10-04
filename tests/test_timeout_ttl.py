"""Timeout/TTL: expiração real dos holds depois de 60s."""

import time

import pytest
from fastapi.testclient import TestClient

from app import HOLD_SECONDS, r


@pytest.mark.timeout_ttl
class TestExpiration:
    def test_hold_tem_ttl_de_60s_no_redis(self, client: TestClient, hold) -> None:
        token, _ = hold([11], "alice")
        ttl = r.ttl(f"seat:11:hold")
        assert 0 < ttl <= HOLD_SECONDS

    def test_expiracao_real_libera_assento(self, client: TestClient, hold) -> None:
        token, _ = hold([12], "bob")
        # Acelera o relógio: TTL = 1s e espera vencer (expiração nativa, não hack de leitura)
        r.expire(f"seat:12:hold", 1)
        r.expire(f"token:{token}", 1)
        time.sleep(1.1)
        seat = next(s for s in client.get("/seats").json() if s["number"] == 12)
        assert seat["status"] == "available"

    def test_confirmar_hold_expirado_falha_404(self, client: TestClient, hold) -> None:
        token, _ = hold([13], "bob")
        r.expire(f"seat:13:hold", 1)
        r.expire(f"token:{token}", 1)
        time.sleep(1.1)
        res = client.post("/confirms", json={"token": token, "user_id": "bob"})
        assert res.status_code == 404

    def test_metadados_do_token_expiram_junto(self, client: TestClient, hold) -> None:
        token, _ = hold([14], "bob")
        r.expire(f"seat:14:hold", 1)
        r.expire(f"token:{token}", 1)
        time.sleep(1.1)
        assert r.exists(f"token:{token}") == 0

    def test_assento_expirado_pode_ser_hold_novamente(self, client: TestClient, hold) -> None:
        token, _ = hold([15], "bob")
        r.expire(f"seat:15:hold", 1)
        r.expire(f"token:{token}", 1)
        time.sleep(1.1)
        res = client.post("/holds", json={"seat_numbers": [15], "user_id": "heidi"})
        assert res.status_code == 201

    def test_ttl_informado_na_listagem_diminui_com_tempo(self, client: TestClient, hold) -> None:
        hold([16], "alice")
        first = next(s for s in client.get("/seats").json() if s["number"] == 16)
        time.sleep(1.1)
        second = next(s for s in client.get("/seats").json() if s["number"] == 16)
        assert second["expires_in_seconds"] < first["expires_in_seconds"]