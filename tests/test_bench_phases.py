from __future__ import annotations

import pytest

from smos.bench import phases
from smos.bench.corpus import CorpusBuilder
from smos.compression.context_builder import build_context


@pytest.fixture(scope="module")
def filled():
    import shutil
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp(prefix="smosbench_"))
    store = phases.open_store(path)
    builder = CorpusBuilder(11)
    report = phases.ingest_to(store, builder, 120)
    yield store, builder, report, path
    store.close()
    shutil.rmtree(path, ignore_errors=True)


def test_ingest_to_reaches_target_and_reports_rate(filled) -> None:
    store, _builder, report, _path = filled
    assert report["quarantined"] == 0
    assert report["added"] >= 100 and report["segments"] == store.count() >= 100
    assert report["seg_per_s"] > 0 and report["seconds"] > 0


def test_ingest_to_is_incremental(filled) -> None:
    store, builder, _report, _path = filled
    before = store.count()
    again = phases.ingest_to(store, builder, 120)  # nothing new to issue
    assert again["added"] == 0 and store.count() == before
    more = phases.ingest_to(store, builder, 180)
    assert 40 <= more["added"] <= 80 and store.count() == before + more["added"]


def test_profile_stages_sums_close_to_total(filled) -> None:
    store, builder, _report, _path = filled
    stages = phases.profile_stages(store, builder.probes(6, max_index=100), repeats=2)
    parts = sum(stages[k] for k in ("embed_ms", "vector_ms", "bm25_ms", "graph_ms", "sqlite_ms", "fusion_ms"))
    assert stages["total_ms"] > 0 and stages["samples"] == 12
    assert parts == pytest.approx(stages["total_ms"], rel=0.10)


def test_timed_context_matches_build_context(filled) -> None:
    store, builder, _report, _path = filled
    for probe in builder.probes(8, max_index=100):
        ids, _timings = phases.timed_context(store, probe.query, 10)
        assert ids == build_context(probe.query, 10, store).sources


def test_compare_hnsw_recall_is_a_fraction_and_exact_is_exact(filled) -> None:
    store, builder, _report, _path = filled
    result = phases.compare_hnsw(store, builder.probes(10, max_index=100))
    assert 0.0 <= result["recall10"] <= 1.0
    assert result["recall10"] > 0.8  # tiny index: HNSW is near-exact
    assert result["flat_vs_flat_recall10"] == 1.0
    assert result["build_s"] > 0 and result["mem_bytes"] > 0 and result["query_ms"] > 0 and result["flat_ms"] > 0
    assert result["recall50"] is None or 0.0 <= result["recall50"] <= 1.0


def test_store_footprint_counts_files(filled) -> None:
    _store, _builder, _report, path = filled
    footprint = phases.store_footprint(path)
    assert footprint["disk_bytes"] > 0 and footprint["index_bytes"] > 0
    assert footprint["disk_bytes"] >= footprint["index_bytes"]
