"""Complexidade algorítmica: escalabilidade de operações com relação a k e n.

Validado por contagem de round-trips Redis e por degradação de latência:
- POST /holds    = O(1) round-trip (1 eval Lua, independente de k)
- POST /confirms = O(1) round-trip (1 eval Lua)
- GET  /seats    = round-trips constantes (pipeline, não 1 comando por assento)
- nenhum endpoint usa SCAN/KEYS (nenhuma operação O(n) sobre o total de chaves)
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import r


class _Spy:
    """Instrumenta r.eval e r.pipeline sem tocar nas demais operações."""

    def __init__(self) -> None:
        self.evals = 0
        self.pipelines = 0
        self._orig_eval = r.eval
        self._orig_pipeline = r.pipeline

    def attach(self) -> None:
        spy = self

        def eval_spy(*a, **kw):
            spy.evals += 1
            return spy._orig_eval(*a, **kw)

        def pipeline_spy(*a, **kw):
            spy.pipelines += 1
            return spy._orig_pipeline(*a, **kw)

        r.eval = eval_spy
        r.pipeline = pipeline_spy

    def detach(self) -> None:
        r.eval = self._orig_eval
        r.pipeline = self._orig_pipeline


@pytest.fixture()
def spy() -> _Spy:
    s = _Spy()
    s.attach()
    yield s
    s.detach()


@pytest.mark.algorithmic
class TestRoundTripComplexity:
    def test_hold_faz_um_unico_round_trip_independente_de_k(self, client: TestClient, spy: _Spy) -> None:
        used = 0
        for k in (1, 5, 14):
            seats = list(range(used + 1, used + k + 1))
            used += k
            spy.evals = spy.pipelines = 0
            res = client.post("/holds", json={"seat_numbers": seats, "user_id": f"k{k}"})
            assert res.status_code == 201, res.text
            total = spy.evals + spy.pipelines
            assert total == 1, f"k={k} -> {total} round-trips"

    def test_confirm_faz_um_unico_round_trip(self, client: TestClient, hold, spy: _Spy) -> None:
        token, _ = hold([2, 4, 6], "alice")
        spy.evals = spy.pipelines = 0
        res = client.post("/confirms", json={"token": token, "user_id": "alice"})
        assert res.status_code == 200
        assert spy.evals + spy.pipelines == 1

    def test_get_seats_usa_pipeline_constante_nao_comando_por_assento(self, client: TestClient, hold, spy: _Spy) -> None:
        for s in range(1, 21):
            hold([s], f"u{s}")
        spy.evals = spy.pipelines = 0
        res = client.get("/seats")
        assert res.status_code == 200
        # Round-trips constantes (~2), jamais proporcional aos 20 assentos ou 20 tokens
        assert spy.pipelines <= 3 and spy.evals == 0

    def test_nenhum_comando_scan_ou_keys_em_operacao_qualquer(self, client: TestClient, hold, spy: _Spy) -> None:
        hold([3], "alice")
        client.post("/confirms", json={"token": client.get("/seats").json()[2], "user_id": "x"})  # 404 esperado
        client.get("/seats")
        # spy não rastreia comandos brutos, então checamos por transmissão: monitor seria caro;
        # garantia estrutural: nenhum código chama r.scan/r.keys
        import inspect
        import app as app_module

        source = inspect.getsource(app_module)
        assert "r.scan" not in source and "r.keys" not in source


@pytest.mark.algorithmic
class TestScalingBehavior:
    def test_hold_com_k_1_vs_k_20_custo_nao_linear(self, client: TestClient) -> None:
        def bench(k: int) -> float:
            samples = []
            for i in range(15):
                start = time.perf_counter_ns()
                client.post("/holds", json={"seat_numbers": list(((i * 7 + j) % 20) + 1 for j in range(k)), "user_id": f"b{i}-{k}"})
                samples.append((time.perf_counter_ns() - start) / 1e6)
            return min(samples)

        k1 = bench(1)
        k20 = bench(20)
        assert k20 < k1 * 2 + 5, f"k1={k1:.1f}ms k20={k20:.1f}ms (crescimento não-linear)"

    def test_concorrencia_12_usuarios_assento_unico_exatamente_1_vencedor(self, client: TestClient) -> None:
        def try_hold(i):
            return client.post("/holds", json={"seat_numbers": [20], "user_id": f"u{i}"}).status_code

        with ThreadPoolExecutor(max_workers=12) as ex:
            codes = list(ex.map(try_hold, range(12)))
        assert codes.count(201) == 1
        assert codes.count(409) == 11

    def test_concorrencia_10_usuarios_assentos_distintos_todos_aceitos(self, client: TestClient) -> None:
        def try_hold(i):
            return client.post("/holds", json={"seat_numbers": [i + 1], "user_id": f"v{i}"}).status_code

        with ThreadPoolExecutor(max_workers=10) as ex:
            codes = list(ex.map(try_hold, range(10)))
        assert all(c == 201 for c in codes)