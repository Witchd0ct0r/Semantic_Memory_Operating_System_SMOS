"""Acceptance test for the offset promise: a hit maps exactly into the document text and onto the parser's blocks."""
from __future__ import annotations

LONG = ("Die Größe der Bremsbeläge ist jährlich zu prüfen, äußerst sorgfältig und öffentlich nachvollziehbar. " * 12) + "\n"

BLOCKS = [
    ("paragraph", "5.2 Prüfung der Bremsen\nDie Bremsen müssen jährlich geprüft werden. Äußerst wichtig ist die Größe der Beläge.\n"),
    ("paragraph", "5.2 Prüfung der Bremsen (cont.)\nWeitere Anforderungen: Ölverlust und Verschleißgrenzen sind zu dokumentieren.\n"),
    ("heading", "5.3\n"),                                                       # under 20 characters
    ("paragraph", "5.3 Lenkung\nDie Lenkung muss spielfrei sein und übermäßige Schwergängigkeit ausschließen.\n"),
    ("paragraph", "5.2 Prüfung der Bremsen\nZweiter Abschnitt mit gleicher Nummer, anderer Text über Reifen und Felgen.\n"),
    ("paragraph", "5.4 Beläge\n" + LONG),                                       # over 800 characters
    ("paragraph", "5.5 Schluss\nDieses Dokument endet hier.\n"),
]
TEXT = "".join(t for _, t in BLOCKS)


def _ingest(client, h) -> str:
    body = {"external_handle": {"system": "norms", "native_id": "ALIGN-1"}, "domain_tags": ["norm:ALIGN"],
            "blocks": [{"block_type": t, "text": x, "heading_level": 1 if t == "heading" else None} for t, x in BLOCKS]}
    r = client.post("/api/v1/documents", json=body, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    return r.json()["doc_id"]


def test_text_blocks_and_segments_align_on_a_real_ingest(api) -> None:
    client, h, app = api
    doc_id = _ingest(client, h)
    body = client.get(f"/api/v1/nodes/{doc_id}/text", params={"include": "blocks"}, headers=h).json()

    # (a) the text is exactly what the parser sent, (f) complete, (g) length in code points
    assert body["text"] == TEXT and body["complete"] is True and body["char_length"] == len(TEXT)
    assert any(ord(c) > 127 for c in TEXT[:60]), "the fixture must put non-ASCII before the checked clauses"

    # (b) blocks tile the text
    blocks = body["blocks"]
    assert [b["ordinal"] for b in blocks] == list(range(len(BLOCKS)))
    assert blocks[0]["char_start"] == 0 and blocks[-1]["char_end"] == len(TEXT)
    assert all(cur["char_start"] == prev["char_end"] for prev, cur in zip(blocks, blocks[1:]))
    assert "".join(TEXT[b["char_start"]:b["char_end"]] for b in blocks) == TEXT

    # segments tile the text and carry exactly their slice of it
    store = app.state.store
    segments = sorted(store.get_many(store.get_segment_ids_by_doc(doc_id)).values(), key=lambda r: r["metadata"]["ordinal"])
    assert len(segments) > len(BLOCKS) - 1          # the over-800-character block was split
    assert segments[0]["metadata"]["char_start"] == 0 and segments[-1]["metadata"]["char_end"] == len(TEXT)
    for seg in segments:
        m = seg["metadata"]
        assert TEXT[m["char_start"]:m["char_end"]] == seg["content"]                        # (c)
    for prev, cur in zip(segments, segments[1:]):
        assert cur["metadata"]["char_start"] == prev["metadata"]["char_end"]

    # (d) every hit lies inside its block range
    by_ordinal = {b["ordinal"]: b for b in blocks}
    seen = set()
    for query in ("Die Bremsen müssen jährlich geprüft werden", "Lenkung spielfrei Schwergängigkeit",
                  "Zweiter Abschnitt mit gleicher Nummer Reifen Felgen", "Größe der Bremsbeläge jährlich prüfen",
                  "Dieses Dokument endet hier", "Ölverlust und Verschleißgrenzen dokumentieren"):
        r = client.post("/api/v1/query", json={"query": query, "k": 10, "tags": ["norm:ALIGN"]}, headers=h)
        for hit in r.json()["results"]:
            m = hit["metadata"]
            seen.add(hit["id"])
            first, last = by_ordinal[m["block_ordinal"]], by_ordinal[m["block_ordinal_end"]]
            assert (m["block_char_start"], m["block_char_end"]) == (first["char_start"], first["char_end"])
            assert first["char_start"] <= m["char_start"] and m["char_end"] <= last["char_end"]
            assert m["document_hash"] == body["document_hash"]
    assert len(seen) >= 5

    # (e) the two clauses that share the heading number resolve to different blocks
    def block_of(query: str) -> int:
        top = client.post("/api/v1/query", json={"query": query, "k": 1}, headers=h).json()["results"][0]
        return top["metadata"]["block_ordinal"]
    assert block_of("Zweiter Abschnitt mit gleicher Nummer, anderer Text über Reifen und Felgen") == 4
    assert block_of("Die Bremsen müssen jährlich geprüft werden. Äußerst wichtig ist die Größe der Beläge") == 0
