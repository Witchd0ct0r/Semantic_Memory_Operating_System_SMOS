from __future__ import annotations

import sqlite3

from smos.memory.graph_store import GraphStore
from smos.storage.migrations import STORE_MIGRATIONS, run_migrations


def migrated_graph(conn: sqlite3.Connection) -> GraphStore:
    """A GraphStore on a connection whose schema comes from the store migrations (as in production)."""
    run_migrations(conn, STORE_MIGRATIONS)
    return GraphStore(conn)


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal valid PDF (Helvetica, WinAnsi) with the given text lines on each page; no PDF library needed."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")  # 1
    add(b"")  # 2, patched below
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")  # 3
    kids = []
    for lines in pages:
        ops = [b"BT", b"/F1 11 Tf", b"14 TL", b"72 750 Td"]
        for line in lines:
            escaped = line.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\(").replace(b")", b"\)")
            ops.append(b"(" + escaped + b") Tj T*")
        ops.append(b"ET")
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                   b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (content, font))
        kids.append(b"%d 0 R" % page)
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
