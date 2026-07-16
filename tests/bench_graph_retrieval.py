"""
bench_graph_retrieval.py — Memory Graph traversal benchmark.

Standalone script (NOT pytest-collected — see tests/test_exact_term_recall.py's
docstring for why files in tests/ named bench_*.py aren't picked up by `pytest
tests/`). Run directly: `python tests/bench_graph_retrieval.py`

Seeds a small corpus of memories that share entities across a chain
(auth -> JWT -> middleware -> auth_service, mirroring the roadmap's own
example), then times traversal at increasing hop depth and reports how many
memories become reachable at each depth versus what plain vector/BM25
retrieval would surface for the same query.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

_CHAIN_DOCS = [
    "The `auth_service` validates incoming requests before routing them.",
    "`auth_service` delegates token checks to the `middleware` layer.",
    "The `middleware` layer parses the `JWT` from the Authorization header.",
    "`JWT` tokens are signed and verified using the `auth` module's secret key.",
    "The `auth` module also handles password hashing and session rotation.",
]

_UNRELATED_DOCS = [
    "PostgreSQL connection pooling reduces per-request handshake overhead.",
    "Kubernetes rolling updates minimize downtime during deploys.",
    "FAISS IndexFlatIP performs brute-force cosine similarity search.",
]


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="smos_bench_graph_"))
    try:
        store = VectorStore(persist_path=tmp_dir / "data")

        ids = []
        for doc in _CHAIN_DOCS + _UNRELATED_DOCS:
            ids.append(store.store(MemoryObject(type="doc", content=doc)))

        start_id = "entity:auth_service"
        print(f"Graph size: {store.graph.node_count()} nodes, {store.graph.edge_count()} edges")
        print(f"Traversing from {start_id!r}:\n")

        for hops in (1, 2, 3, 4):
            t0 = time.perf_counter()
            neighbors = store.graph.traverse(start_id, max_hops=hops)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            memory_hits = [n for n in neighbors if n["type"] == "memory"]
            entity_hits = [n for n in neighbors if n["type"] == "entity"]
            print(
                f"  max_hops={hops}: {len(memory_hits)} memories, "
                f"{len(entity_hits)} entities reached in {elapsed_ms:.2f}ms"
            )

        vector_results = store.query("auth service", k=5)
        print(f"\nFor comparison — plain vector query 'auth service' (k=5) "
              f"surfaced {len(vector_results)} results without graph traversal.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
