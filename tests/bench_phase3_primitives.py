"""
bench_phase3_primitives.py — Agent Memory Primitives latency benchmark.

Standalone script (NOT pytest-collected — see tests/test_exact_term_recall.py's
docstring for why files in tests/ named bench_*.py aren't picked up by `pytest
tests/`). Run directly: `python tests/bench_phase3_primitives.py`

Seeds a small corpus, then times each of the 8 agent primitives and each
recall() strategy, reporting per-call latency.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.memory.recall_engine import recall
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.agent_primitives import compress, forget, learn, link, reflect, remember

_SEED_DOCS = [
    "The `auth_service` validates incoming requests before routing them.",
    "`auth_service` delegates token checks to the `middleware` layer.",
    "The `middleware` layer parses the `JWT` from the Authorization header.",
    "PostgreSQL uses MVCC for concurrent transaction isolation.",
    "Redis implements a single-threaded event loop for high throughput.",
]


def _timed(label: str, fn, *args) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn(*args)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  {label:<20} {elapsed_ms:7.2f}ms")
    return elapsed_ms, result


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="smos_bench_phase3_"))
    try:
        store = VectorStore(persist_path=tmp_dir / "data")
        seed_ids = [store.store(MemoryObject(type="doc", content=d)) for d in _SEED_DOCS]

        print("=== Phase 3 Primitives Benchmark ===\n")
        print("Primitives:")
        _, remember_id = _timed("remember", remember, "Agent noticed a new failure pattern in prod.", "episodic", store)
        _, learn_id = _timed("learn", learn, "The retry backoff must be exponential, not linear.", store)
        _, link_result = _timed("link", link, remember_id, learn_id, "related_to", store)
        _, compress_id = _timed("compress", compress, seed_ids[:2], store)
        _, reflect_id = _timed("reflect", reflect, "what do I know about auth?", 5, store)
        _, forget_result = _timed("forget", forget, remember_id, store)

        print("\nrecall() strategies:")
        _timed("recall(hybrid)", recall, "auth middleware", "hybrid", 5, "", "", "", "", store)
        _timed("recall(repo)", recall, "auth middleware", "repo", 5, "", "", "", "", store)
        _timed("recall(chain)", recall, "", "chain", 2, "", "", seed_ids[0], "", store)
        _timed("recall(temporal)", recall, "auth middleware", "temporal", 5, "", "", "", "", store)

        print(f"\nSeed corpus size: {len(_SEED_DOCS)}")
        print(f"compress() produced: {compress_id}")
        print(f"reflect() produced: {reflect_id}")
        print(f"forget() result: {forget_result}")
        print(f"link() result: {link_result}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
