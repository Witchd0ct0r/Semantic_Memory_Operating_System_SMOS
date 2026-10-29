from __future__ import annotations

import re

from smos.bench.corpus import CorpusBuilder
from smos.ingest.contract import IngestionPayload, validate_payload
from smos.tools.semantic_tools import _MIN_INPUT_CHARS as MIN_QUERY_CHARS

_MARKER = re.compile(r"REQ-\d{6}")


def _texts(docs: list[dict]) -> list[str]:
    return [b["text"] for d in docs for b in d["blocks"] if b["block_type"] == "paragraph"]


def test_same_seed_same_documents() -> None:
    a, b = CorpusBuilder(7).next_documents(300), CorpusBuilder(7).next_documents(300)
    assert a == b


def test_different_seed_differs() -> None:
    assert CorpusBuilder(1).next_documents(100) != CorpusBuilder(2).next_documents(100)


def test_incremental_issue_has_no_overlap_or_gaps() -> None:
    builder = CorpusBuilder(3)
    first = builder.next_documents(100)
    second = builder.next_documents(300)
    handles = [d["external_handle"]["native_id"] for d in first + second]
    assert len(handles) == len(set(handles))
    assert len(_texts(first)) == 100
    assert len(_texts(first + second)) == 300
    assert builder.next_documents(300) == []  # nothing more to issue at the same target


def test_documents_pass_validate_payload() -> None:
    for doc in CorpusBuilder(5).next_documents(2000)[:50]:
        assert validate_payload(IngestionPayload(**doc)) == []


def test_paragraphs_fit_one_segment() -> None:
    assert all(len(t) <= 800 for t in _texts(CorpusBuilder(5).next_documents(500)))


def test_segment_ids_unique() -> None:
    markers = [_MARKER.search(t).group(0) for t in _texts(CorpusBuilder(9).next_documents(400))]
    assert len(markers) == len(set(markers)) == 400


def test_needle_marker_present_in_issued_text() -> None:
    builder = CorpusBuilder(4)
    docs = builder.next_documents(600)
    everything = " ".join(_texts(docs))
    needles = [p for p in builder.probes(60, max_index=500) if p.marker]
    assert needles
    assert all(p.marker in everything and p.index is not None and p.index <= 500 for p in needles)


def test_probes_are_deterministic_and_long_enough() -> None:
    first, second = CorpusBuilder(4).probes(40), CorpusBuilder(4).probes(40)
    assert first == second
    assert all(len(p.query.strip()) >= MIN_QUERY_CHARS for p in first)
    assert any(p.marker is None for p in first) and any(p.marker for p in first)


def test_links_point_at_earlier_documents_and_documents_have_tags() -> None:
    docs = CorpusBuilder(6).next_documents(1500)
    seen: set[str] = set()
    for doc in docs:
        for link in doc["source_links"]:
            assert link["target_native_id"] in seen
        assert doc["domain_tags"] and all("," not in t for t in doc["domain_tags"])
        seen.add(doc["external_handle"]["native_id"])
    assert any(d["source_links"] for d in docs)
