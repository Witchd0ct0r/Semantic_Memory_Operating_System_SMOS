"""
bench_full_evolution.py — pre-Phase-1 baseline vs current (Phase 1+2+3) comparison.

Standalone script (NOT pytest-collected — pytest.ini restricts collection to
`test_*.py`; see tests/test_exact_term_recall.py's docstring for why a
bench_*.py file is never picked up by `pytest tests/`). Run directly:
    python tests/bench_full_evolution.py

"Pre-Phase-1" = commit 23f051c (v0.1.4, tagged "repository ingestion
subsystem" — the last commit before 294f844, the first BM25 commit).
Reproduced on the CURRENT tree by monkeypatching store.bm25.query and
store.graph.traverse to return no results, rather than checking out the old
commit — same no-drift rationale as bench_phase2_vs_phase1.py: it isolates
exactly the two retrieval mechanisms Phases 1 and 2 added (BM25 hybrid, graph
expansion) against otherwise-identical code, with no risk of unrelated
refactors between commits confounding the comparison. Phase 3 added no new
retrieval mechanism to the default path (recall() strategies are opt-in;
`hybrid` delegates unchanged to build_compressed_context) — its contribution
is measured separately, in tool-surface growth.

Reuses the two corpora this repo already built and validated for exactly
this purpose: the exact-term corpus from test_exact_term_recall.py (what
BM25 fixed) and the graph-rescue scenarios from bench_phase2_vs_phase1.py
(what the graph fixed) — rather than inventing new ones, so these numbers
stay consistent with the project's existing regression gates.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.compression.context_builder import build_compressed_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from tests.bench_phase2_vs_phase1 import _NOISE_DOCS as _GRAPH_NOISE_DOCS
from tests.bench_phase2_vs_phase1 import _SCENARIOS as _GRAPH_SCENARIOS
from tests.test_exact_term_recall import _EXACT_TERMS, _seed_corpus

_PRE_PHASE1_COMMIT = "23f051c"

# ---------------------------------------------------------------------------
# 1. Exact-term recall — what BM25 (Phase 1) fixed
# ---------------------------------------------------------------------------


def _exact_term_recall(store: VectorStore, ids: dict[str, str]) -> float:
    hits = 0
    for term, _content, _noise in _EXACT_TERMS:
        result = build_compressed_context(term, k=3, store=store)
        if ids[term] in result.sources:
            hits += 1
    return hits / len(_EXACT_TERMS)


def _exact_term_comparison(tmp_root: Path) -> tuple[float, float]:
    print("=" * 72)
    print(f"1. EXACT-TERM RECALL ({len(_EXACT_TERMS)} identifiers, k=3, "
          f"{len(_EXACT_TERMS) * 60} noise docs)")
    print("=" * 72)

    store = VectorStore(persist_path=tmp_root / "exact_term")
    ids = _seed_corpus(store)

    real_bm25_query = store.bm25.query
    real_graph_traverse = store.graph.traverse

    store.bm25.query = lambda text, k: []
    store.graph.traverse = lambda *args, **kwargs: []
    pre_phase1_recall = _exact_term_recall(store, ids)

    store.bm25.query = real_bm25_query
    store.graph.traverse = real_graph_traverse
    now_recall = _exact_term_recall(store, ids)

    print(f"Pre-Phase-1 ({_PRE_PHASE1_COMMIT}, vector-only): {pre_phase1_recall:.2f} "
          f"({int(pre_phase1_recall * len(_EXACT_TERMS))}/{len(_EXACT_TERMS)})")
    print(f"Now (BM25 hybrid + graph):            {now_recall:.2f} "
          f"({int(now_recall * len(_EXACT_TERMS))}/{len(_EXACT_TERMS)})")
    print()
    return pre_phase1_recall, now_recall


# ---------------------------------------------------------------------------
# 2. Graph-rescue recall — what the memory graph (Phase 2) fixed
# ---------------------------------------------------------------------------


def _run_graph_scenario(scenario: dict, tmp_root: Path, bm25_enabled: bool, graph_enabled: bool) -> bool:
    suffix = f"{scenario['name']}_{bm25_enabled}_{graph_enabled}"
    store = VectorStore(persist_path=tmp_root / f"graph_{abs(hash(suffix))}")
    if not bm25_enabled:
        store.bm25.query = lambda text, k: []
    if not graph_enabled:
        store.graph.traverse = lambda *args, **kwargs: []

    target_id = store.store(MemoryObject(type="doc", content=scenario["target"]))
    store.store(MemoryObject(type="doc", content=scenario["anchor"]))
    for noise in _GRAPH_NOISE_DOCS:
        store.store(MemoryObject(type="doc", content=noise))

    result = build_compressed_context(scenario["query"], k=1, store=store)
    return target_id in result.sources


def _graph_rescue_comparison(tmp_root: Path) -> tuple[float, float]:
    n = len(_GRAPH_SCENARIOS)
    print("=" * 72)
    print(f"2. GRAPH-RESCUE RECALL ({n} scenarios, k=1 — target shares only an "
          f"entity link with the top hit)")
    print("=" * 72)
    print(f"{'Scenario':<38} {'Pre-Phase-1':<14} {'Now'}")
    print("-" * 72)

    pre_phase1_hits = 0
    now_hits = 0
    for scenario in _GRAPH_SCENARIOS:
        pre_ok = _run_graph_scenario(scenario, tmp_root, bm25_enabled=False, graph_enabled=False)
        now_ok = _run_graph_scenario(scenario, tmp_root, bm25_enabled=True, graph_enabled=True)
        pre_phase1_hits += pre_ok
        now_hits += now_ok
        print(f"{scenario['name']:<38} {'FOUND' if pre_ok else 'missed':<14} {'FOUND' if now_ok else 'missed'}")

    pre_recall = pre_phase1_hits / n
    now_recall = now_hits / n
    print("-" * 72)
    print(f"Pre-Phase-1 ({_PRE_PHASE1_COMMIT}, vector-only): {pre_recall:.2f} ({pre_phase1_hits}/{n})")
    print(f"Now (BM25 hybrid + graph):            {now_recall:.2f} ({now_hits}/{n})")
    print()
    return pre_recall, now_recall


# ---------------------------------------------------------------------------
# 3. MCP tool-surface growth — what all three phases added
# ---------------------------------------------------------------------------

_PRE_PHASE1_TOOL_COUNT = 10  # verified via: git show 23f051c:smos/server.py | grep -c '@app.tool()'


def _tool_surface_comparison() -> tuple[int, int]:
    server_path = Path(__file__).parent.parent / "smos" / "server.py"
    now_count = server_path.read_text(encoding="utf-8").count("@app.tool()")

    print("=" * 72)
    print("3. MCP TOOL SURFACE")
    print("=" * 72)
    print(f"Pre-Phase-1 ({_PRE_PHASE1_COMMIT}): {_PRE_PHASE1_TOOL_COUNT} tools")
    print(f"Now:                    {now_count} tools "
          f"(+{now_count - _PRE_PHASE1_TOOL_COUNT}, "
          f"{100 * (now_count - _PRE_PHASE1_TOOL_COUNT) / _PRE_PHASE1_TOOL_COUNT:.0f}% growth)")
    print()
    return _PRE_PHASE1_TOOL_COUNT, now_count


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_full_evolution_"))
    try:
        exact_pre, exact_now = _exact_term_comparison(tmp_root)
        graph_pre, graph_now = _graph_rescue_comparison(tmp_root)
        tools_pre, tools_now = _tool_surface_comparison()

        print("=" * 72)
        print("SUMMARY")
        print("=" * 72)
        print(f"Exact-term recall:   {exact_pre:.0%} -> {exact_now:.0%}")
        print(f"Graph-rescue recall: {graph_pre:.0%} -> {graph_now:.0%}")
        print(f"MCP tools:           {tools_pre} -> {tools_now}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
