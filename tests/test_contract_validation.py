from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    quarantine_payload,
    validate_payload,
)
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _valid_payload(**overrides) -> IngestionPayload:
    defaults = dict(
        external_handle=ExternalHandle(system="wiki", native_id="doc-1"),
        blocks=[ContentBlock(block_type="paragraph", text="Hello world.")],
    )
    defaults.update(overrides)
    return IngestionPayload(**defaults)


def test_valid_payload_has_no_errors():
    assert validate_payload(_valid_payload()) == []


def test_empty_blocks_list_is_rejected():
    errors = validate_payload(_valid_payload(blocks=[]))
    assert any("blocks" in e and "at least one block" in e for e in errors)


def test_blank_block_text_is_rejected():
    payload = _valid_payload(blocks=[ContentBlock(block_type="paragraph", text="   ")])
    errors = validate_payload(payload)
    assert any("text must not be empty" in e for e in errors)


def test_heading_block_without_heading_level_is_rejected():
    payload = _valid_payload(blocks=[ContentBlock(block_type="heading", text="# T")])
    errors = validate_payload(payload)
    assert any("heading_level" in e for e in errors)


def test_heading_block_with_valid_level_passes():
    payload = _valid_payload(
        blocks=[ContentBlock(block_type="heading", text="# T", heading_level=1)]
    )
    assert validate_payload(payload) == []


def test_empty_external_handle_system_is_rejected():
    payload = _valid_payload(external_handle=ExternalHandle(system="", native_id="doc-1"))
    errors = validate_payload(payload)
    assert any("external_handle.system" in e for e in errors)


def test_unknown_source_link_edge_type_is_rejected():
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="not_a_real_type")]
    )
    errors = validate_payload(payload)
    assert any("unknown edge_type" in e for e in errors)


def test_known_core_edge_type_passes():
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="references")]
    )
    assert validate_payload(payload) == []


def test_adapter_declared_edge_type_passes():
    class _FakeAdapter:
        name = "fake"
        edge_types = frozenset({"satisfies"})

        def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
            return []

        def extract_references(self, text: str) -> list[tuple[str, str, str]]:
            return []

        def llm_boundary_prompt(self):
            return None

    register_adapter(_FakeAdapter())
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="satisfies")]
    )
    assert validate_payload(payload) == []


def test_quarantine_payload_persists_raw_payload_and_errors(store: VectorStore) -> None:
    payload = _valid_payload(blocks=[])
    errors = validate_payload(payload)
    key = quarantine_payload(store, payload, errors)
    row = store.get_quarantine(key)
    assert row is not None
    assert row["system"] == "wiki"
    assert row["native_id"] == "doc-1"
    assert "at least one block" in row["errors"]
