from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from smos.memory.schemas import MemoryObject
from smos.tools.agent_primitives import compress, forget, learn, link, remember, snapshot


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.store.return_value = "mock-id-001"
    return store


def test_remember_calls_store(mock_store: MagicMock) -> None:
    result = remember("The service uses Redis for caching.", "episodic", mock_store)
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "episodic"
    assert call_arg.content == "The service uses Redis for caching."


def test_remember_rejects_short_content(mock_store: MagicMock) -> None:
    result = remember("short", "episodic", mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_forget_calls_delete(mock_store: MagicMock) -> None:
    mock_store.delete.return_value = True
    result = forget("some-id", mock_store)
    assert result is True
    mock_store.delete.assert_called_once_with("some-id")


def test_learn_sets_semantic_type_and_confidence(mock_store: MagicMock) -> None:
    result = learn("PostgreSQL uses MVCC for isolation.", mock_store)
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "semantic"
    assert call_arg.confidence == 1.0


def test_learn_rejects_short_content(mock_store: MagicMock) -> None:
    result = learn("short", mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_link_rejects_invalid_edge_type(mock_store: MagicMock) -> None:
    result = link("id_a", "id_b", "not_a_real_type", mock_store)
    assert result["success"] is False
    mock_store.graph.add_edge.assert_not_called()


def test_link_rejects_missing_nodes(mock_store: MagicMock) -> None:
    mock_store.graph.get_node.return_value = None
    result = link("id_a", "id_b", "references", mock_store)
    assert result["success"] is False
    mock_store.graph.add_edge.assert_not_called()


def test_link_adds_edge_for_valid_nodes(mock_store: MagicMock) -> None:
    mock_store.graph.get_node.return_value = {"id": "id_a", "type": "memory"}
    result = link("id_a", "id_b", "references", mock_store)
    assert result["success"] is True
    mock_store.graph.add_edge.assert_called_once_with("id_a", "id_b", "references")


def test_snapshot_delegates_to_ingest(mock_store: MagicMock) -> None:
    with patch(
        "smos.tools.agent_primitives.do_semantic_snapshot_repo",
        return_value={"status": "ok"},
    ) as mock_snap:
        result = snapshot("/repo/root", mock_store)
    assert result == {"status": "ok"}
    mock_snap.assert_called_once_with("/repo/root", mock_store)


def test_compress_merges_sources_and_links_edges(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.side_effect = lambda uid: {
        "id": uid,
        "content": f"content for {uid}",
        "metadata": {"type": "doc", "timestamp": "t", "tags": "", "tier": "hot",
                     "source_ids": [], "confidence": 1.0},
    }
    mock_store.store.return_value = "compressed-id-1"
    with patch(
        "smos.tools.agent_primitives.compress_memories_full",
        return_value=("Merged summary.", 0.8, "abstractive"),
    ):
        result = compress(["id1", "id2"], mock_store)

    assert result == "compressed-id-1"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "compressed"
    assert call_arg.content == "Merged summary."
    assert call_arg.source_ids == ["id1", "id2"]
    assert call_arg.confidence == 0.8
    assert mock_store.graph.add_edge.call_count == 2
    mock_store.graph.add_edge.assert_any_call("compressed-id-1", "id1", "generated_from")
    mock_store.graph.add_edge.assert_any_call("compressed-id-1", "id2", "generated_from")


def test_compress_skips_missing_sources(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.side_effect = lambda uid: (
        None if uid == "missing" else {"id": uid, "content": "real content", "metadata": {}}
    )
    mock_store.store.return_value = "compressed-id-2"
    with patch(
        "smos.tools.agent_primitives.compress_memories_full",
        return_value=("Summary.", 0.5, "abstractive"),
    ):
        result = compress(["missing", "id2"], mock_store)
    assert result == "compressed-id-2"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.source_ids == ["id2"]


def test_compress_empty_ids_returns_empty(mock_store: MagicMock) -> None:
    result = compress([], mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_compress_all_sources_missing_returns_empty(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.return_value = None
    result = compress(["missing1", "missing2"], mock_store)
    assert result == ""
    mock_store.store.assert_not_called()
