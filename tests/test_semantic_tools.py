from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from smos.memory.schemas import MemoryObject, RetrievedContext, RetrievedItem
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


def _ctx(ids: list[str], confidence: float = 0.5) -> RetrievedContext:
    return RetrievedContext(
        results=[RetrievedItem(id=i, content=f"content {i}") for i in ids],
        sources=ids,
        confidence=confidence,
        mode="extractive" if ids else "uncertain",
    )


def test_semantic_query_returns_context_dict(mock_store: MagicMock) -> None:
    expected = _ctx(["id1", "id2"], 0.87)
    with patch("smos.tools.semantic_tools.build_context", return_value=expected):
        result = semantic_query("database storage", 5, mock_store)

    assert isinstance(result, dict)
    assert [r["id"] for r in result["results"]] == ["id1", "id2"]
    assert result["sources"] == ["id1", "id2"]
    assert result["confidence"] == pytest.approx(0.87)
    assert "summary" not in result


def test_semantic_query_no_results(mock_store: MagicMock) -> None:
    with patch("smos.tools.semantic_tools.build_context", return_value=_ctx([], 0.0)):
        result = semantic_query("unknown topic", 5, mock_store)

    assert result["confidence"] == 0.0
    assert result["sources"] == []
    assert result["mode"] == "uncertain"


def test_semantic_query_bumps_recall(mock_store: MagicMock) -> None:
    with patch("smos.tools.semantic_tools.build_context", return_value=_ctx(["id1", "id2"])):
        semantic_query("database storage", 5, mock_store)
    mock_store.bump_recall.assert_called_once_with(["id1", "id2"])
