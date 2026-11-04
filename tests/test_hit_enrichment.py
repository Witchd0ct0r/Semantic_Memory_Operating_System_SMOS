from __future__ import annotations

from tests.test_api_routes import _store
from tests.test_document_blocks import BLOCKS

TEXT = "".join(t for _, t in BLOCKS)


def _post_doc(client, h, native_id: str = "D1") -> str:
    body = {"external_handle": {"system": "s", "native_id": native_id},
            "blocks": [{"block_type": t, "text": x, "heading_level": 1 if t == "heading" else None} for t, x in BLOCKS]}
    r = client.post("/api/v1/documents", json=body, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    return r.json()["doc_id"]


def _query(client, h, text: str, k: int = 3) -> list[dict]:
    r = client.post("/api/v1/query", json={"query": text, "k": k}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["results"]


def _doc_hash(client, h, doc_id: str) -> str:
    docs = client.get("/api/v1/documents", headers=h).json()["documents"]
    return next(d["document_hash"] for d in docs if d["id"] == doc_id)


def test_segment_hit_carries_block_range_and_hash(api) -> None:
    client, h, _app = api
    doc_id = _post_doc(client, h)
    hit = _query(client, h, "The maximum permitted deflection shall not exceed two millimetres.")[0]
    meta = hit["metadata"]
    start = len(BLOCKS[0][1]) + len(BLOCKS[1][1]) + len(BLOCKS[2][1])
    assert meta["doc_id"] == doc_id
    assert (meta["block_ordinal"], meta["block_ordinal_end"], meta["block_type"]) == (3, 3, "paragraph")
    assert (meta["block_char_start"], meta["block_char_end"]) == (start, start + len(BLOCKS[3][1]))
    assert meta["document_hash"] == _doc_hash(client, h, doc_id)
    assert isinstance(hit["score"], float)


def test_hit_without_blocks_has_null_fields(api) -> None:
    client, h, app = api
    doc_id = _post_doc(client, h)
    note = _store(client, h, "A plain note about calibrating the braking test rig regularly.")
    app.state.store.delete_blocks(doc_id)                                  # a pre-0.5.0 document
    seg = _query(client, h, "The maximum permitted deflection shall not exceed two millimetres.")[0]["metadata"]
    assert seg["doc_id"] == doc_id and seg["document_hash"]
    for key in ("block_ordinal", "block_ordinal_end", "block_char_start", "block_char_end", "block_type"):
        assert key in seg and seg[key] is None
    plain = next(x for x in _query(client, h, "calibrating the braking test rig", k=5) if x["id"] == note)["metadata"]
    for key in ("document_hash", "block_ordinal", "block_ordinal_end", "block_char_start", "block_char_end", "block_type"):
        assert key in plain and plain[key] is None


def test_merged_short_block_hit_reports_end_ordinal(api) -> None:
    client, h, _app = api
    _post_doc(client, h)
    hits = _query(client, h, "1 Scope This standard applies to widgets used in vehicles of all kinds.", k=5)
    first = next(x for x in hits if x["metadata"]["ordinal"] == 0)["metadata"]
    # the segmenter merged both short heading blocks (0 and 2) into the paragraph block 1: one segment, blocks 0..2
    assert first["block_ordinal"] == 0 and first["block_ordinal_end"] == 2
    assert first["block_char_start"] == 0 and first["block_char_end"] == len(BLOCKS[0][1])


def test_text_endpoint_returns_hash_length_and_blocks(api) -> None:
    client, h, _app = api
    doc_id = _post_doc(client, h)
    plain = client.get(f"/api/v1/nodes/{doc_id}/text", headers=h).json()
    assert plain["text"] == TEXT and plain["complete"] is True
    assert plain["char_length"] == len(TEXT) and plain["document_hash"] == _doc_hash(client, h, doc_id)
    assert "blocks" not in plain
    with_blocks = client.get(f"/api/v1/nodes/{doc_id}/text", params={"include": "blocks"}, headers=h).json()
    blocks = with_blocks["blocks"]
    assert "".join(TEXT[b["char_start"]:b["char_end"]] for b in blocks) == TEXT
    assert [b["block_type"] for b in blocks] == [t for t, _ in BLOCKS]


def test_complete_false_when_first_segment_does_not_start_at_zero(api) -> None:
    client, h, app = api
    doc_id = _post_doc(client, h)
    first = app.state.store.get_segment_by_ordinal(doc_id, 0)
    app.state.store.delete(first["id"])
    body = client.get(f"/api/v1/nodes/{doc_id}/text", headers=h).json()
    assert body["complete"] is False
