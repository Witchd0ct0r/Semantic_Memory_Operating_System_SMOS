from __future__ import annotations

import threading
from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _payload(native_id: str, text: str = "Some requirement text for the lock test.") -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="sys", native_id=native_id),
        blocks=[ContentBlock(block_type="paragraph", text=text)],
    )


def test_locks_are_released_after_distinct_ingests(store: VectorStore) -> None:
    for i in range(25):
        do_ingest_document_schema(_payload(f"DOC-{i}"), store)
    assert store._doc_locks == {}


def test_quarantined_payload_leaves_no_lock_behind(store: VectorStore) -> None:
    bad = IngestionPayload(external_handle=ExternalHandle(system="sys", native_id="BAD"), blocks=[])
    assert do_ingest_document_schema(bad, store)["status"] == "quarantined"
    assert store._doc_locks == {}


def test_lock_released_when_the_body_raises(store: VectorStore) -> None:
    with pytest.raises(RuntimeError):
        with store.document_lock("doc-x"):
            raise RuntimeError("boom")
    assert store._doc_locks == {}
    with store.document_lock("doc-x"):  # still usable afterwards
        pass


def test_same_document_is_still_mutually_exclusive(store: VectorStore) -> None:
    inside = 0
    overlap = []
    guard = threading.Lock()

    def worker() -> None:
        nonlocal inside
        with store.document_lock("same"):
            with guard:
                inside += 1
                overlap.append(inside)
            threading.Event().wait(0.02)
            with guard:
                inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(overlap) == 1
    assert store._doc_locks == {}
