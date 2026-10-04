"""Padrões: OpenAPI, status codes, convenções REST e documentação de contrato."""

import pytest
from fastapi.testclient import TestClient

from app import app


@pytest.mark.standards
class TestOpenAPI:
    def test_schema_openapi_disponivel(self, client: TestClient) -> None:
        res = client.get("/openapi.json")
        assert res.status_code == 200
        schema = res.json()
        assert schema["info"]["title"] == "Seat Hold Service"

    def test_endpoints_documentados(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths) == {"/seats", "/holds", "/confirms", "/users/{user_id}/seats"}

    def test_modelos_documentados(self, client: TestClient) -> None:
        components = client.get("/openapi.json").json()["components"]["schemas"]
        for model in ("SeatOut", "HoldOut", "HoldRequest", "ConfirmRequest", "CancelRequest", "UserSeatsOut"):
            assert model in components