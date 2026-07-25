from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_save_and_get_quarantine_round_trips(store: VectorStore) -> None:
    store.save_quarantine(
        key="q1",
        system="wiki",
        native_id="doc-1",
        raw_payload='{"external_handle": {"system": "wiki", "native_id": "doc-1"}}',
        errors='["blocks: must contain at least one block"]',
    )
    row = store.get_quarantine("q1")
    assert row is not None
    assert row["system"] == "wiki"
    assert row["native_id"] == "doc-1"
    assert "must contain at least one block" in row["errors"]


def test_get_quarantine_returns_none_for_unknown_key(store: VectorStore) -> None:
    assert store.get_quarantine("does-not-exist") is None
