"""
bench_phase2_vs_phase1.py — Phase 2 (Memory Graph) vs Phase 1 (BM25 hybrid) comparison.

Standalone script (NOT pytest-collected — pytest.ini restricts collection to
`test_*.py`; see tests/test_exact_term_recall.py's docstring for why a
bench_*.py file is never picked up by `pytest tests/`). Run directly:
    python tests/bench_phase2_vs_phase1.py

Measures the two things Phase 2 actually changed relative to Phase 1's shipped
state (commit b890a19):

  1. RETRIEVAL RECALL — the graph-expansion step in context_builder.py is the
     only behavioral change to the read path. "Phase 1 state" is reproduced
     exactly (not approximately) by monkeypatching store.graph.traverse() to
     return [] — the expansion block still runs but appends nothing, which is
     bit-for-bit what build_compressed_context did before Phase 2 (Task 5 of
     the Phase 2 plan added nothing else to the ranking/scoring logic). This
     is more precise than checking out the old commit, because it isolates
     the single variable (graph on/off) against identical BM25/vector/RRF
     code — no risk of unrelated drift between commits confounding the
     comparison.

     Each scenario seeds an "anchor" memory (top RRF hit for the query) and a
     "target" memory that shares one `entity:` node with the anchor but is
     lexically/semantically unrelated to the query — vector and BM25 alone
     have no signal to rank it inside k=1. This isolates exactly what the
     graph can find that hybrid retrieval structurally cannot, regardless of
     corpus tuning (same principle as test_exact_term_recall.py's
     test_hybrid_recall_beats_vector_only, one phase up).

  2. WRITE-PATH LATENCY — Phase 2 added synchronous entity extraction + graph
     node/edge writes inside VectorStore.store(), inside the same lock BM25
     and FAISS updates already use. "Phase 1 state" is reproduced by
     monkeypatching VectorStore._update_graph to a no-op on an otherwise
     identical store() call, isolating the graph's own added cost from
     embedding/FAISS/BM25 cost (which Phase 2 did not touch).
"""
from __future__ import annotations

import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.compression.context_builder import build_compressed_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

# ---------------------------------------------------------------------------
# 1. Retrieval recall — graph-rescue scenarios
# ---------------------------------------------------------------------------

_NOISE_DOCS = [
    "PostgreSQL connection pooling reduces per-request handshake overhead.",
    "Kubernetes rolling updates minimize downtime during deploys.",
    "FAISS IndexFlatIP performs brute-force cosine similarity search.",
    "The weekly deploy freeze starts every Friday at 5pm.",
    "Coffee consumption in the office kitchen peaks around 10am.",
    "The office thermostat is set to 21 degrees Celsius by default.",
    "Quarterly planning documents are archived in the shared drive.",
    "The onboarding checklist includes setting up VPN access.",
]

_SCENARIOS = [
    {
        "name": "auth_service / SessionToken",
        "query": "auth_service",
        "anchor": "The `auth_service` module authenticates incoming API requests and issues `SessionToken` objects.",
        "target": "`SessionToken` values are cached in Redis with a five-minute TTL to reduce database load.",
    },
    {
        "name": "PaymentProcessor / TransactionEvent",
        "query": "PaymentProcessor",
        "anchor": "`PaymentProcessor` charges the customer's card and emits a `TransactionEvent`.",
        "target": "`TransactionEvent` records are streamed to the analytics pipeline for fraud detection.",
    },
    {
        "name": "ImageUploader / BlobStorage",
        "query": "ImageUploader",
        "anchor": "`ImageUploader` resizes photos and stores them via `BlobStorage`.",
        "target": "`BlobStorage` uses content-addressable hashing to deduplicate identical files.",
    },
    {
        "name": "NotificationQueue / RetryWorker",
        "query": "NotificationQueue",
        "anchor": "`NotificationQueue` batches outbound emails and hands them to `RetryWorker`.",
        "target": "`RetryWorker` uses exponential backoff capped at five attempts before giving up.",
    },
    {
        "name": "SearchIndexer / InvertedIndex",
        "query": "SearchIndexer",
        "anchor": "`SearchIndexer` tokenizes documents and updates the `InvertedIndex`.",
        "target": "`InvertedIndex` compaction merges small segment files during idle periods.",
    },
]


def _run_recall_scenario(scenario: dict, tmp_root: Path, graph_enabled: bool) -> bool:
    """Seed one scenario's corpus fresh, query k=1, return whether target was recalled."""
    store = VectorStore(persist_path=tmp_root / f"data_{abs(hash(scenario['name'] + str(graph_enabled)))}")
    if not graph_enabled:
        store.graph.traverse = lambda *args, **kwargs: []  # reproduces pre-Phase-2 behavior exactly

    target_id = store.store(MemoryObject(type="doc", content=scenario["target"]))
    store.store(MemoryObject(type="doc", content=scenario["anchor"]))
    for noise in _NOISE_DOCS:
        store.store(MemoryObject(type="doc", content=noise))

    result = build_compressed_context(scenario["query"], k=1, store=store)
    return target_id in result.sources


def _recall_comparison(tmp_root: Path) -> None:
    print("=" * 72)
    print("1. RETRIEVAL RECALL — graph-only-reachable memories (k=1 per query)")
    print("=" * 72)
    print(f"{'Scenario':<38} {'Phase 1 (graph off)':<22} {'Phase 2 (graph on)'}")
    print("-" * 72)

    phase1_hits = 0
    phase2_hits = 0
    for scenario in _SCENARIOS:
        phase1_ok = _run_recall_scenario(scenario, tmp_root, graph_enabled=False)
        phase2_ok = _run_recall_scenario(scenario, tmp_root, graph_enabled=True)
        phase1_hits += phase1_ok
        phase2_hits += phase2_ok
        print(f"{scenario['name']:<38} {'FOUND' if phase1_ok else 'missed':<22} {'FOUND' if phase2_ok else 'missed'}")

    n = len(_SCENARIOS)
    print("-" * 72)
    print(f"Phase 1 (graph off) recall: {phase1_hits}/{n} ({100 * phase1_hits / n:.0f}%)")
    print(f"Phase 2 (graph on)  recall: {phase2_hits}/{n} ({100 * phase2_hits / n:.0f}%)")
    print()


# ---------------------------------------------------------------------------
# 2. Write-path latency — entity extraction + graph writes overhead
# ---------------------------------------------------------------------------

_WRITE_BENCH_DOCS = [
    f"The `Component{i}` module handles `Task{i}` processing and reports to `Monitor{i}`."
    for i in range(150)
]
_WARMUP_CALLS = 10  # discarded — first calls pay one-time FAISS/sqlite file-creation cost
_TRIALS = 3  # store() latency is dominated by ~10-12ms of embedding cost; the graph's own
             # share is small enough that a single pass is noise-dominated (a first version
             # of this benchmark measured graph-on as *faster* than graph-off, which isn't
             # a real effect — repeat trials and report the isolated per-call delta instead
             # of trusting one run's mean.


def _time_store_calls(tmp_root: Path, graph_enabled: bool, trial: int) -> list[float]:
    store = VectorStore(persist_path=tmp_root / f"writebench_{graph_enabled}_{trial}")
    if not graph_enabled:
        store._update_graph = lambda *args, **kwargs: None  # isolates graph's own cost

    times_ms = []
    for content in _WRITE_BENCH_DOCS:
        t0 = time.perf_counter()
        store.store(MemoryObject(type="doc", content=content))
        times_ms.append((time.perf_counter() - t0) * 1000)
    return times_ms[_WARMUP_CALLS:]


def _write_path_comparison(tmp_root: Path) -> None:
    n_measured = len(_WRITE_BENCH_DOCS) - _WARMUP_CALLS
    print("=" * 72)
    print(f"2. WRITE-PATH LATENCY — {_TRIALS} trials x {n_measured} measured store() calls "
          f"({_WARMUP_CALLS} warmup calls discarded per trial)")
    print("=" * 72)

    # Interleave off/on trials so any monotonic drift (disk cache warming further,
    # thermal throttling, etc.) doesn't bias one condition over the other.
    phase1_trial_means = []
    phase2_trial_means = []
    for trial in range(_TRIALS):
        phase1_trial_means.append(statistics.mean(_time_store_calls(tmp_root, False, trial)))
        phase2_trial_means.append(statistics.mean(_time_store_calls(tmp_root, True, trial)))

    p1_mean = statistics.mean(phase1_trial_means)
    p2_mean = statistics.mean(phase2_trial_means)
    per_trial_deltas = [p2 - p1 for p1, p2 in zip(phase1_trial_means, phase2_trial_means)]
    overhead_ms = statistics.mean(per_trial_deltas)
    overhead_pct = (overhead_ms / p1_mean) * 100 if p1_mean else float("nan")

    print(f"Phase 1 (graph off) — mean {p1_mean:.2f}ms/store across trial means {[f'{t:.2f}' for t in phase1_trial_means]}")
    print(f"Phase 2 (graph on)  — mean {p2_mean:.2f}ms/store across trial means {[f'{t:.2f}' for t in phase2_trial_means]}")
    print(f"Graph overhead: {overhead_ms:+.3f}ms/store ({overhead_pct:+.1f}%), "
          f"per-trial deltas: {[f'{d:+.2f}' for d in per_trial_deltas]}")
    if abs(overhead_ms) < statistics.pstdev(per_trial_deltas or [0]):
        print("NOTE: overhead is smaller than the spread between trials — not reliably "
              "distinguishable from zero at this sample size; embedding cost "
              "(~10-12ms/call) dominates store() latency and dwarfs the graph's own cost.")
    print()


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_phase2_vs_phase1_"))
    try:
        _recall_comparison(tmp_root)
        _write_path_comparison(tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
