from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import zipfile
from pathlib import Path

import faiss
import pytest

from smos.admin import pentest, tokens
from smos.admin.backup import BackupError, create_backup, validate_backup
from smos.admin.storeops import StoreOpRefused
from smos.memory.schemas import MemoryObject

MEMBERS = ("manifest.json", "metadata.db", "faiss.index")
_counter = itertools.count()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def filled(api):
    client, headers, app = api
    for text in ("alpha backup sentence one", "beta backup sentence two", "gamma backup sentence three"):
        app.state.store.store(MemoryObject(type="doc", content=text))
    return client, headers, app


def _read(zip_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(zip_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _write(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _variant(tmp_path: Path, good: Path, *, db=None, index=None, manifest=None, extra=None, drop=None,
             rehash: bool = True) -> Path:
    """A copy of a good backup with one thing changed; hashes are recomputed unless rehash=False."""
    members = _read(good)
    meta = json.loads(members["manifest.json"])
    if db is not None:
        members["metadata.db"] = db
    if index is not None:
        members["faiss.index"] = index
    if rehash:
        meta["sha256"] = {"metadata.db": _sha(members["metadata.db"]), "faiss.index": _sha(members["faiss.index"])}
    if manifest:
        meta.update(manifest)
    members["manifest.json"] = json.dumps(meta).encode()
    for name in drop or ():
        members.pop(name)
    members.update(extra or {})
    return _write(tmp_path / f"variant-{next(_counter)}.zip", members)


@pytest.fixture
def good(filled, tmp_path: Path) -> Path:
    _c, _h, app = filled
    path = create_backup(app)
    yield path
    path.unlink(missing_ok=True)


def test_round_trip_validates_and_manifest_matches_store(filled, good, tmp_path: Path) -> None:
    _c, _h, app = filled
    work = tmp_path / "work"
    work.mkdir()
    manifest = validate_backup(good, work)
    assert manifest["format"] == 1 and manifest["counts"] == {"memories": 3, "vectors": 3}
    assert manifest["smos_version"] and manifest["created_at"]
    assert (work / "metadata.db").exists() and (work / "faiss.index").exists()
    assert sorted(_read(good)) == sorted(MEMBERS)
    assert app.state.store_op_active is False  # the API is not left paused


def test_backup_holds_no_admin_content(filled, good) -> None:
    _c, _h, app = filled
    token_hash = app.state.admin_db.execute("SELECT token_hash FROM tokens").fetchone()[0]
    blob = b"".join(_read(good).values())
    assert token_hash.encode() not in blob and b"password_hash" not in blob


def _bad_cases(tmp_path: Path, good: Path) -> dict[str, Path]:
    members = _read(good)
    db_bytes, index_bytes = members["metadata.db"], members["faiss.index"]
    newer = tmp_path / "newer.db"
    newer.write_bytes(db_bytes)
    conn = sqlite3.connect(str(newer))
    conn.execute("UPDATE schema_version SET version = 999")
    conn.commit()
    conn.close()
    empty_index = tmp_path / "empty.index"
    loaded = faiss.read_index(str(_dump(tmp_path, index_bytes)))
    faiss.write_index(faiss.IndexIDMap(faiss.IndexFlatIP(loaded.d)), str(empty_index))
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(good.read_bytes()[:200])
    notzip = tmp_path / "notzip.zip"
    notzip.write_bytes(b"this is not a zip file at all")
    return {
        "extra member": _variant(tmp_path, good, extra={"admin.db": b"x"}),
        "path traversal": _variant(tmp_path, good, extra={"../evil": b"x"}),
        "absolute path member": _variant(tmp_path, good, drop=("metadata.db",), extra={"/metadata.db": db_bytes}),
        "missing member": _variant(tmp_path, good, drop=("faiss.index",)),
        "wrong hash": _variant(tmp_path, good, db=db_bytes + b"\0", rehash=False),
        "truncated": truncated,
        "not a zip": notzip,
        "format 2": _variant(tmp_path, good, manifest={"format": 2}),
        "newer schema": _variant(tmp_path, good, db=newer.read_bytes()),
        "vector count differs": _variant(tmp_path, good, index=empty_index.read_bytes()),
        "corrupt database": _variant(tmp_path, good, db=b"not a database" * 100),
    }


def _dump(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "dump.index"
    path.write_bytes(data)
    return path


_REASONS = {
    "extra member": "exactly manifest.json",
    "path traversal": "exactly manifest.json",
    "absolute path member": "exactly manifest.json",
    "missing member": "exactly manifest.json",
    "wrong hash": "checksum",
    "truncated": "damaged|not a SMOS backup",
    "not a zip": "not a zip",
    "format 2": "Unsupported backup format",
    "newer schema": "newer SMOS",
    "vector count differs": "inconsistent",
    "corrupt database": "not readable|integrity",
}


def test_every_bad_backup_is_refused_for_the_right_reason(good, tmp_path: Path) -> None:
    cases = _bad_cases(tmp_path, good)
    assert set(cases) == set(_REASONS)
    for label, path in cases.items():
        work = tmp_path / ("work-" + label.replace(" ", "-"))
        work.mkdir()
        with pytest.raises(BackupError, match=_REASONS[label]):
            validate_backup(path, work)
        assert not (tmp_path / "evil").exists(), label  # nothing was written outside the work folder


def test_creation_refused_in_pentest_mode(filled) -> None:
    _c, _h, app = filled
    pentest.enable(app)
    with pytest.raises(StoreOpRefused):
        create_backup(app)
    assert app.state.store_op_active is False


def test_snapshot_is_consistent_while_store_is_written(filled, tmp_path: Path) -> None:
    _c, _h, app = filled
    dest = tmp_path / "snap"
    dest.mkdir()
    app.state.store.write_snapshot(dest)
    app.state.store.store(MemoryObject(type="doc", content="written after the snapshot"))
    conn = sqlite3.connect(str(dest / "metadata.db"))
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3
    conn.close()
    assert faiss.read_index(str(dest / "faiss.index")).ntotal == 3
