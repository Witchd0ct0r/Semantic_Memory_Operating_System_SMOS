from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from smos.memory.schemas import CompressedContext, MemoryObject
from smos.tools.semantic_tools import semantic_query, semantic_store, semantic_write


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.store.return_value = "mock-id-001"
    return store


def test_semantic_store_calls_store(mock_store: MagicMock) -> None:
    result = semantic_store("Redis is an in-memory data store.", "doc", mock_store)
    assert result == "mock-id-001"
    mock_store.store.assert_called_once()
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "doc"
    assert call_arg.content == "Redis is an in-memory data store."


def test_semantic_store_type_preserved(mock_store: MagicMock) -> None:
    semantic_store("Architecture decision.", "adr", mock_store)
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "adr"


def test_semantic_write_tags_preserved(mock_store: MagicMock) -> None:
    result = semantic_write("adr", "Use PostgreSQL.", mock_store, tags=["database", "adr"])
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert "database" in call_arg.tags
    assert "adr" in call_arg.tags


def test_semantic_write_no_tags(mock_store: MagicMock) -> None:
    result = semantic_write("log", "Deployment completed.", mock_store)
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.tags == []


def test_semantic_query_returns_compressed_dict(mock_store: MagicMock) -> None:
    expected = CompressedContext(
        summary="PostgreSQL is used for primary storage.",
        sources=["id1", "id2"],
        confidence=0.87,
    )
    with patch("smos.tools.semantic_tools.build_compressed_context", return_value=expected):
        result = semantic_query("database storage", 5, mock_store)

    assert isinstance(result, dict)
    assert result["summary"] == "PostgreSQL is used for primary storage."
    assert result["sources"] == ["id1", "id2"]
    assert result["confidence"] == pytest.approx(0.87)


def test_semantic_query_no_results(mock_store: MagicMock) -> None:
    empty = CompressedContext(summary="No relevant memories found.", sources=[], confidence=0.0)
    with patch("smos.tools.semantic_tools.build_compressed_context", return_value=empty):
        result = semantic_query("unknown topic", 5, mock_store)

    assert result["confidence"] == 0.0
    assert result["sources"] == []
