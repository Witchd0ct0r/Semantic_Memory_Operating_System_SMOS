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


def _payload(marker: str) -> IngestionPayload:
    blocks = [ContentBlock(block_type="paragraph", text=f"Paragraph {i} of version {marker}. " * 8 + "\n")
              for i in range(40)]
    return IngestionPayload(external_handle=ExternalHandle(system="sys", native_id="SAME"), blocks=blocks)


def test_concurrent_reingest_of_same_handle_leaves_one_consistent_version(store: VectorStore) -> None:
    for round_no in range(3):
        barrier = threading.Barrier(2)
        results: list[dict] = []

        def run(marker: str) -> None:
            payload = _payload(f"{marker}{round_no}")
            barrier.wait()
            results.append(do_ingest_document_schema(payload, store))

        threads = [threading.Thread(target=run, args=(m,)) for m in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        doc_id = results[0]["doc_id"]
        segment_ids = store.get_segment_ids_by_doc(doc_id)
        texts = [store.get_by_uuid(i)["content"] for i in segment_ids]
        joined = "".join(texts)
        versions = {m for m in ("A", "B") if f"version {m}{round_no}." in joined}
        assert len(versions) == 1, f"round {round_no}: mixed versions {versions}"
        assert len(segment_ids) == len(set(segment_ids))
        expected = _payload(f"{next(iter(versions))}{round_no}")
        assert joined == "".join(b.text for b in expected.blocks)
