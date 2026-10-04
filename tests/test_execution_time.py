"""Tempo de execução: budget de latência por endpoint (p50/p99).

Budgets (local, TestClient, mesma máquina):
- GET  /seats     : 20 KEYS em pipeline + HGETALL -> p99 < 150ms
- POST /holds     : 1 Lua + 1 round-trip          -> p99 < 100ms
- POST /confirms  : 1 Lua + 1 round-trip          -> p99 < 100ms
"""

import time

import pytest
from fastapi.testclient import TestClient

SEATS_SAMPLE = 30
N_ROUNDS = 12  # 2 warmups + 10 medidos


def _timed(fn) -> list[float]:
    samples = []
    for i in range(N_ROUNDS):
        start = time.perf_counter_ns()
        fn()
        if i >= 2:  # descarta warmups
            samples.append((time.perf_counter_ns() - start) / 1e6)
    return samples


def _p(values: list[float], q: float) -> float:
    return sorted(values)[min(int(q * len(values)), len(values) - 1)]


@pytest.mark.performance
class TestLatencyBudget:
    def test_get_seats_p99_abaixo_de_150ms(self, client: TestClient, hold) -> None:
        hold([1, 5, 20], "alice")  # estado não-vazio
        lat = _timed(lambda: client.get("/seats"))
        print(f"\n/seats     p50={_p(lat, .5):.1f}ms p99={_p(lat, .99):.1f}ms")
        assert _p(lat, .99) < 150

    def test_hold_p99_abaixo_de_100ms(self, client: TestClient) -> None:
        rounds = (iter(range(100, 100 + SEATS_SAMPLE)),)
        latencies = []
        for s in [1 + i % 20 for i in range(SEATS_SAMPLE)]:
            start = time.perf_counter_ns()
            client.post("/holds", json={"seat_numbers": [((s - 1) % 20) + 1], "user_id": f"u{s}"})
            latencies.append((time.perf_counter_ns() - start) / 1e6)
        print(f"\n/holds     p50={_p(latencies, .5):.1f}ms p99={_p(latencies, .99):.1f}ms")
        assert _p(latencies, .99) < 100

    def test_confirm_p99_abaixo_de_100ms(self, client: TestClient, hold) -> None:
        hold_by_user = {f"u{s}": hold([s], f"u{s}")[0] for s in range(1, 11)}
        latencies = []
        for user, t in hold_by_user.items():
            start = time.perf_counter_ns()
            client.post("/confirms", json={"token": t, "user_id": user})
            latencies.append((time.perf_counter_ns() - start) / 1e6)
        print(f"\n/confirms  p50={_p(latencies, .5):.1f}ms p99={_p(latencies, .99):.1f}ms")
        assert _p(latencies, .99) < 100