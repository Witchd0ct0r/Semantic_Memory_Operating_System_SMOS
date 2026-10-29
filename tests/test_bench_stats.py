from __future__ import annotations

import pytest

from smos.bench.stats import (
    find_knee,
    peak_throughput,
    percentile,
    project_size_at_budget,
    summarize_latencies,
    sustainable_step,
)


def _step(clients: int, rps: float, p95: float = 10.0, errors: int = 0, limited: bool = False) -> dict:
    return {"clients": clients, "rps": rps, "p95_ms": p95, "errors": errors,
            "requests": int(rps * 10), "client_limited": limited}


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([10.0], 99) == 10.0
    assert percentile([1, 2, 3, 4, 5], 0) == 1
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    assert percentile([5, 1, 4, 2, 3], 50) == 3  # input need not be sorted


def test_percentile_empty_raises() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize_empty_has_n_zero() -> None:
    s = summarize_latencies([])
    assert s["n"] == 0
    assert s["p50"] is None and s["p95"] is None and s["p99"] is None and s["max"] is None


def test_summarize_fills_every_key() -> None:
    s = summarize_latencies([float(i) for i in range(1, 101)])
    assert s["n"] == 100
    assert s["p50"] == pytest.approx(50.5)
    assert s["p95"] == pytest.approx(95.05)
    assert s["p99"] == pytest.approx(99.01)
    assert s["max"] == 100.0
    assert s["mean"] == pytest.approx(50.5)


def test_peak_ignores_client_limited_steps() -> None:
    steps = [_step(1, 10), _step(8, 40), _step(64, 90, limited=True)]
    assert peak_throughput(steps)["clients"] == 8
    assert peak_throughput([_step(1, 10, limited=True)]) is None
    assert peak_throughput([]) is None


def test_sustainable_requires_zero_errors_and_p95_within_budget() -> None:
    steps = [_step(1, 10, p95=20), _step(8, 40, p95=150), _step(16, 50, p95=400),
             _step(32, 55, p95=100, errors=3)]
    best = sustainable_step(steps, budget_ms=200)
    assert best["clients"] == 8
    assert sustainable_step([_step(1, 10, p95=900)], budget_ms=200) is None


def test_knee_found_when_growth_flattens() -> None:
    steps = [_step(1, 10), _step(2, 19), _step(4, 35), _step(8, 40), _step(16, 40.5)]
    assert find_knee(steps) == 8


def test_knee_none_when_always_growing() -> None:
    assert find_knee([_step(1, 10), _step(2, 20), _step(4, 40)]) is None
    assert find_knee([_step(1, 10)]) is None


def test_projection_linear() -> None:
    points = [(1000.0, 10.0), (2000.0, 20.0), (3000.0, 30.0)]
    assert project_size_at_budget(points, 100.0) == pytest.approx(10000.0)


def test_projection_none_when_flat_or_short() -> None:
    assert project_size_at_budget([(1000.0, 10.0), (2000.0, 20.0)], 100.0) is None
    assert project_size_at_budget([(1000.0, 10.0), (2000.0, 10.0), (3000.0, 10.0)], 100.0) is None
    assert project_size_at_budget([(1000.0, 30.0), (2000.0, 20.0), (3000.0, 10.0)], 100.0) is None
