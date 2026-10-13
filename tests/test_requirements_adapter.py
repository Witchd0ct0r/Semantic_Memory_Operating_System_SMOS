from __future__ import annotations

import importlib

import pytest

from smos.ingest.adapters.registry import (
    all_edge_types,
    get_adapter,
    register_adapter,
    reset_registry,
)
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, SourceLink, validate_payload


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _payload(edge_type: str) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="cb", native_id="1"),
        blocks=[ContentBlock(block_type="paragraph", text="The system shall stop.")],
        source_links=[SourceLink(target_native_id="2", edge_type=edge_type)],
    )


def test_adapter_declares_traceability_edge_types():
    adapter = RequirementsAdapter()
    assert adapter.name == "requirements"
    assert adapter.edge_types == frozenset({"parent_of", "member_of", "cites"})


def test_adapter_has_no_text_behaviour():
    adapter = RequirementsAdapter()
    assert adapter.structural_boundaries("Some text.") == []
    assert adapter.extract_references("See [REQ:12].") == []
    assert adapter.llm_boundary_prompt() is None


def test_new_edge_types_are_rejected_until_the_adapter_is_registered():
    errors = validate_payload(_payload("parent_of"))
    assert any("unknown edge_type 'parent_of'" in e for e in errors)

    register_adapter(RequirementsAdapter())
    assert validate_payload(_payload("parent_of")) == []
    assert validate_payload(_payload("member_of")) == []
    assert validate_payload(_payload("cites")) == []


def test_core_depends_on_needs_no_registration():
    assert validate_payload(_payload("depends_on")) == []


def test_package_import_registers_requirements_adapter():
    import smos.ingest.adapters as adapters_pkg

    importlib.reload(adapters_pkg)
    assert get_adapter("requirements") is not None
    assert {"parent_of", "member_of", "cites"} <= all_edge_types()
