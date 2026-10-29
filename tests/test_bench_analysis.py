from __future__ import annotations

import copy

import pytest

from smos.bench import analysis
from smos.bench.analysis import compare_runs, hnsw_verdict, key_metrics
from tests.benchdata import make_result


def test_verdict_not_needed() -> None:
    v = hnsw_verdict(make_result(), 200.0)
    assert v["level"] == "not_needed"
    assert v["facts"]["p95_ms"] == 38.0 and v["facts"]["budget_ms"] == 200.0


def test_verdict_watch_when_projection_under_three_times() -> None:
    # p95 grows 40 ms per 1k segments: the 200 ms budget is reached near 5.5k, well inside 3x the 3k measured
    result = make_result([1000, 2000, 3000], [40.0, 80.0, 120.0])
    v = hnsw_verdict(result, 200.0)
    assert v["level"] == "watch"
    assert v["facts"]["projected_segments"] == pytest.approx(5000.0, rel=0.05)


def test_verdict_consider_when_over_budget_and_vector_share_high() -> None:
    result = make_result([1000, 5000, 10000], [100.0, 200.0, 400.0], vector_ms=30.0, embed_ms=10.0, other_ms=10.0)
    v = hnsw_verdict(result, 200.0)
    assert v["level"] == "consider"
    assert v["facts"]["vector_share"] == pytest.approx(0.6)


def test_verdict_not_hnsw_names_dominant_stage() -> None:
    result = make_result([1000, 5000, 10000], [100.0, 200.0, 400.0], vector_ms=2.0, embed_ms=300.0, other_ms=10.0)
    v = hnsw_verdict(result, 200.0)
    assert v["level"] == "not_hnsw"
    assert v["facts"]["dominant_stage"] == "embedding"
    assert "embedding" in v["headline"]


def test_verdict_insufficient_with_two_sizes() -> None:
    v = hnsw_verdict(make_result([1000, 5000], [20.0, 30.0]), 200.0)
    assert v["level"] == "insufficient"


def test_verdict_notes_small_range() -> None:
    v = hnsw_verdict(make_result([500, 1000, 2500, 5000], [10.0, 11.0, 12.0, 13.0]), 200.0)
    assert v["facts"]["range_small"] is True


def test_verdict_facts_include_recall_and_delete_caveat() -> None:
    v = hnsw_verdict(make_result(recall10=0.968), 200.0)
    assert v["facts"]["recall10"] == pytest.approx(0.968)
    assert "delete" in v["facts"]["caveat"].lower()
    assert v["facts"]["hnsw_build_s"] == 3.0


def test_key_metrics_come_from_largest_size_and_sweep() -> None:
    m = key_metrics(make_result())
    assert m["peak_rps"] == 41.0
    assert m["sustainable_rps"] == 40.0  # the 41 req/s step has p95 450 ms, over the 200 ms budget
    assert m["query_p95_ms"] == 38.0
    assert m["ingest_seg_per_s"] == 80.0
    assert m["startup_s"] == 5.0


def _row(rows: list[dict], key: str) -> dict:
    return next(r for r in rows if r["key"] == key)


def test_compare_flags_regression_over_20_percent() -> None:
    previous, current = make_result(startup_s=6.0), make_result(startup_s=9.0)
    row = _row(compare_runs(current, previous), "startup_s")
    assert row["flag"] == "regression" and row["change_pct"] == pytest.approx(50.0)


def test_compare_slower_between_10_and_20() -> None:
    row = _row(compare_runs(make_result(startup_s=5.75), make_result(startup_s=5.0)), "startup_s")
    assert row["flag"] == "slower"


def test_compare_higher_is_better_for_throughput() -> None:
    fast, slow = make_result(ingest_rate=100.0), make_result(ingest_rate=60.0)
    assert _row(compare_runs(slow, fast), "ingest_seg_per_s")["flag"] == "regression"
    assert _row(compare_runs(fast, slow), "ingest_seg_per_s")["flag"] == "better"


def test_compare_small_change_is_ok() -> None:
    assert _row(compare_runs(make_result(startup_s=5.2), make_result(startup_s=5.0)), "startup_s")["flag"] == "ok"


def test_compare_without_previous_is_na() -> None:
    rows = compare_runs(make_result(), None)
    assert rows and all(r["flag"] == "n/a" and r["previous"] is None for r in rows)


def test_compare_tolerates_missing_metric() -> None:
    broken = copy.deepcopy(make_result())
    broken["scaling"][-1].pop("startup_s")
    row = _row(compare_runs(broken, make_result()), "startup_s")
    assert row["flag"] == "n/a"


def test_every_compared_metric_has_a_definition() -> None:
    for row in compare_runs(make_result(), make_result()):
        assert analysis.METRIC_DEFINITIONS[row["key"]]
