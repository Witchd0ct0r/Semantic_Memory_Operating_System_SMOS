from __future__ import annotations

from smos.memory.schemas import MemoryObject


def test_memory_object_defaults_source_ids_empty() -> None:
    m = MemoryObject(type="doc", content="Some content here.")
    assert m.source_ids == []


def test_memory_object_defaults_confidence_one() -> None:
    m = MemoryObject(type="doc", content="Some content here.")
    assert m.confidence == 1.0


def test_memory_object_accepts_explicit_source_ids_and_confidence() -> None:
    m = MemoryObject(
        type="compressed",
        content="Merged content.",
        source_ids=["id1", "id2"],
        confidence=0.75,
    )
    assert m.source_ids == ["id1", "id2"]
    assert m.confidence == 0.75
