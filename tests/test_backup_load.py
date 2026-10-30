from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import faiss
import pytest

from smos.admin import pentest
from smos.admin.backup import BackupError, create_backup, load_backup, recover_interrupted_swap
from smos.admin.storeops import StoreOpRefused, paused_api
from smos.api.app import create_app
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.storage.migrations import STORE_MIGRATIONS, run_migrations

PASSWORD = "correct-horse-1"
LIVE_FILES = ("metadata.db", "faiss.index")


def _hashes(data_dir: Path) -> dict[str, str]:
    return {n: hashlib.sha256((data_dir / n).read_bytes()).hexdigest() for n in LIVE_FILES}


def _memories(app) -> int:
    return app.state.store.stats()["memories"]


def _add(app, text: str) -> None:
    app.state.store.store(MemoryObject(type="doc", content=text))


@pytest.fixture
def filled(api):
    client, headers, app = api
    for text in ("alpha load sentence one", "beta load sentence two", "gamma load sentence three"):
        _add(app, text)
    app.state.store._save_index()  # the index file only exists on disk after a save
    return client, headers, app


@pytest.fixture
def backup(filled):
    _c, _h, app = filled
    path = create_backup(app)
    yield path
    path.unlink(missing_ok=True)


def _no_leftovers(data_dir: Path) -> None:
    assert not (data_dir / "store-safety").exists() and not (data_dir / "store-swap.json").exists()
    assert not list(data_dir.glob("load-tmp-*"))


def test_round_trip_replaces_the_store_with_the_backup(filled, backup) -> None:
    client, headers, app = filled
    data_dir = app.state.data_dir
    _add(app, "delta written after the backup")
    old = app.state.store

    manifest = load_backup(app, backup)
    assert manifest["counts"]["memories"] == 3
    assert app.state.store is not old and _memories(app) == 3
    hits = app.state.store.query("delta written after the backup", k=5)
    assert all("delta" not in h["content"] for h in hits)
    _no_leftovers(data_dir)
    assert app.state.store_op_active is False
    assert client.post("/api/v1/query", json={"query": "alpha load sentence one"}, headers=headers).status_code == 200
    _add(app, "the loaded store accepts writes")  # lifecycle attached, connection usable
    assert _memories(app) == 4


def test_refused_files_leave_the_live_store_untouched(filled, backup, tmp_path: Path) -> None:
    _c, _h, app = filled
    data_dir = app.state.data_dir
    before, store = _hashes(data_dir), app.state.store
    garbage = tmp_path / "garbage.zip"
    garbage.write_bytes(b"nope")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(backup) as src, zipfile.ZipFile(tampered, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name) + (b"x" if name == "metadata.db" else b""))
    for bad in (garbage, tampered):
        with pytest.raises(BackupError):
            load_backup(app, bad)
        assert _hashes(data_dir) == before and app.state.store is store
        assert app.state.store_op_active is False
        _no_leftovers(data_dir)
    assert _memories(app) == 3


def test_failure_after_the_swap_restores_the_previous_store(filled, backup, monkeypatch) -> None:
    client, headers, app = filled
    data_dir = app.state.data_dir
    _add(app, "delta written after the backup")  # 4 memories live, 3 in the backup

    def fail(self):
        raise RuntimeError("simulated post-load check failure")

    monkeypatch.setattr(VectorStore, "health_check", fail)
    with pytest.raises(BackupError, match="previous store was restored"):
        load_backup(app, backup)
    monkeypatch.undo()
    assert _memories(app) == 4  # the original data is back
    _no_leftovers(data_dir)
    assert app.state.store_op_active is False
    assert client.post("/api/v1/query", json={"query": "delta written"}, headers=headers).status_code == 200


def test_load_refused_in_pentest_mode_and_while_another_operation_runs(filled, backup) -> None:
    _c, _h, app = filled
    before = _hashes(app.state.data_dir)
    with paused_api(app):
        with pytest.raises(StoreOpRefused):
            load_backup(app, backup)
    assert _hashes(app.state.data_dir) == before
    pentest.enable(app)
    frozen = _hashes(app.state.data_dir)  # closing the live store checkpointed it; from here it must not change
    with pytest.raises(StoreOpRefused):
        load_backup(app, backup)
    assert _hashes(app.state.data_dir) == frozen
    pentest.disable(app)
    assert _memories(app) == 3


def test_interrupted_swap_is_rolled_back_at_startup(filled) -> None:
    _c, _h, app = filled
    data_dir = app.state.data_dir
    app.state.close()
    original = _hashes(data_dir)  # taken after the close: closing checkpoints the database
    safety = data_dir / "store-safety"
    safety.mkdir()
    for name in LIVE_FILES:  # the crash happened after the live files were moved aside and half a copy was made
        (data_dir / name).replace(safety / name)
    (data_dir / "metadata.db").write_bytes(b"half copied")
    (data_dir / "store-swap.json").write_text(json.dumps({"safety": "store-safety"}), encoding="utf-8")

    recover_interrupted_swap(data_dir)
    assert _hashes(data_dir) == original
    _no_leftovers(data_dir)

    again = create_app(data_dir, admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    try:
        assert _memories(again) == 3
    finally:
        again.state.close()


def test_marker_without_safety_copy_only_removes_the_marker(filled) -> None:
    _c, _h, app = filled
    data_dir = app.state.data_dir
    app.state.close()
    before = _hashes(data_dir)
    (data_dir / "store-swap.json").write_text("{}", encoding="utf-8")
    recover_interrupted_swap(data_dir)
    assert _hashes(data_dir) == before and not (data_dir / "store-swap.json").exists()


def test_a_backup_from_an_older_schema_loads_and_is_migrated(filled, tmp_path: Path) -> None:
    _c, _h, app = filled
    older = tmp_path / "older"
    older.mkdir()
    conn = sqlite3.connect(str(older / "metadata.db"))
    run_migrations(conn, STORE_MIGRATIONS[:-1], older / "metadata.db")
    conn.commit()
    conn.close()
    dim = faiss.read_index(str(app.state.data_dir / "faiss.index")).d
    faiss.write_index(faiss.IndexIDMap(faiss.IndexFlatIP(dim)), str(older / "faiss.index"))
    manifest = {"format": 1, "smos_version": "old", "created_at": "t", "counts": {"memories": 0, "vectors": 0},
                "sha256": {n: hashlib.sha256((older / n).read_bytes()).hexdigest() for n in LIVE_FILES}}
    path = tmp_path / "older.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for n in LIVE_FILES:
            zf.write(older / n, n)

    load_backup(app, path)
    assert _memories(app) == 0
    version = app.state.store._db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == STORE_MIGRATIONS[-1][0]
    _no_leftovers(app.state.data_dir)


def test_crash_right_after_the_marker_never_deletes_the_live_store(filled) -> None:
    """Codex 9, High: marker and empty safety folder exist, nothing was moved yet."""
    _c, _h, app = filled
    data_dir = app.state.data_dir
    app.state.close()
    original = _hashes(data_dir)
    (data_dir / "store-safety").mkdir()
    (data_dir / "store-swap.json").write_text("{}", encoding="utf-8")
    recover_interrupted_swap(data_dir)
    assert _hashes(data_dir) == original
    _no_leftovers(data_dir)


def test_crash_after_moving_only_some_files_restores_the_originals(filled) -> None:
    _c, _h, app = filled
    data_dir = app.state.data_dir
    app.state.close()
    original = _hashes(data_dir)
    safety = data_dir / "store-safety"
    safety.mkdir()
    (data_dir / "metadata.db").replace(safety / "metadata.db")  # faiss.index never made it into the safety folder
    (data_dir / "metadata.db").write_bytes(b"half copied new database")
    (data_dir / "metadata.db-wal").write_bytes(b"stale wal of the half-loaded store")
    (data_dir / "store-swap.json").write_text("{}", encoding="utf-8")
    recover_interrupted_swap(data_dir)
    assert _hashes(data_dir) == original
    assert not (data_dir / "metadata.db-wal").exists()  # a side file of the new store must not meet the old database
    _no_leftovers(data_dir)


def test_close_failing_at_the_start_of_a_load_keeps_the_original_store(filled, backup, monkeypatch) -> None:
    _c, _h, app = filled
    data_dir = app.state.data_dir
    _add(app, "delta written after the backup")
    real_close = app.state.store.close

    def failing_close():
        monkeypatch.undo()
        real_close()
        raise RuntimeError("close failed")

    monkeypatch.setattr(app.state.store, "close", failing_close)
    with pytest.raises(BackupError, match="previous store was restored"):
        load_backup(app, backup)
    assert _memories(app) == 4
    _no_leftovers(data_dir)
    assert app.state.store_op_active is False
