"""VectorStore and GraphStore share one SQLite connection; every use of it must be under one lock.

Two independent locks let one thread's commit end another thread's half-finished transaction, which surfaced as
`OperationalError: cannot commit - no transaction is active` and `SystemError: error return without exception set`
on queries that ran while a document was being ingested (found by the benchmark's error analytics).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


def _run(workers: list, seconds: float = 3.0) -> list[str]:
    stop, errors = threading.Event(), []

    def wrap(work):
        def go() -> None:
            try:
                while not stop.is_set():
                    work()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
        return go

    threads = [threading.Thread(target=wrap(w)) for w in workers]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(30)
    return errors


def test_graph_writes_and_recall_updates_do_not_interleave(tmp_path: Path) -> None:
    store = VectorStore(persist_path=tmp_path / "data")
    memory = MemoryObject(type="doc", content="a stored memory about brakes", timestamp=datetime.utcnow())
    store.store(memory)
    counter = iter(range(10 ** 9))

    def graph_write() -> None:
        i = next(counter)
        store.graph.add_node(f"n{i}", "memory", f"n{i}")
        store.graph.add_edge(f"n{i}", memory.id, "references")

    def recall_update() -> None:
        store.bump_recall([memory.id])

    errors = _run([graph_write, graph_write, recall_update, recall_update])
    assert errors == []


def test_graph_reads_do_not_end_a_recall_transaction(tmp_path: Path) -> None:
    store = VectorStore(persist_path=tmp_path / "data")
    memory = MemoryObject(type="doc", content="a stored memory about brakes", timestamp=datetime.utcnow())
    store.store(memory)
    counter = iter(range(10 ** 9))

    def graph_write() -> None:
        store.graph.add_node(f"m{next(counter)}", "memory", "x")

    def recall_then_read() -> None:
        store.bump_recall([memory.id])
        store.graph.get_node(memory.id)

    assert _run([graph_write, recall_then_read, recall_then_read]) == []
