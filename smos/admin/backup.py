"""Store backup files: create one, and check one before it may replace the live store.

A backup is a zip of exactly three members: manifest.json, metadata.db and faiss.index. It never
holds admin.db (tokens, invitations, usage log, admin accounts).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import faiss

import smos
from smos.admin.storeops import StoreOpRefused, open_store, paused_api
from smos.storage.migrations import STORE_MIGRATIONS, SchemaTooNewError, run_migrations

log = logging.getLogger(__name__)

FORMAT = 1
MEMBERS = ("manifest.json", "metadata.db", "faiss.index")
_MAX_MANIFEST_BYTES = 1_000_000
_CHUNK = 1024 * 1024


class BackupError(ValueError):
    """The file is not a usable backup; the message says why in plain words."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup(app) -> Path:
    """Write a backup zip into the data directory and return its path; the caller deletes it.
    The API is paused only while the snapshot is taken, not while the zip is compressed."""
    if app.state.pentest:
        raise StoreOpRefused("Backups are off while pentest mode is on: the open store is the throwaway one.")
    data_dir = Path(app.state.live_dir)
    scratch = Path(tempfile.mkdtemp(prefix="backup-tmp-", dir=data_dir))
    try:
        with paused_api(app):
            app.state.store.write_snapshot(scratch)
        conn = sqlite3.connect(str(scratch / "metadata.db"))
        try:
            memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            conn.close()
        manifest = {
            "format": FORMAT,
            "smos_version": smos.__version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"memories": memories, "vectors": faiss.read_index(str(scratch / "faiss.index")).ntotal},
            "sha256": {name: _sha256(scratch / name) for name in MEMBERS[1:]},
        }
        zip_path = data_dir / f"backup-{secrets.token_hex(8)}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            for name in MEMBERS[1:]:
                zf.write(scratch / name, name)
        return zip_path
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def validate_backup(zip_path: Path, workdir: Path) -> dict:
    """Check a backup completely without touching the live store. Returns the manifest and leaves the
    verified, already-migrated metadata.db and faiss.index in `workdir`. Raises BackupError."""
    zip_path, workdir = Path(zip_path), Path(workdir)
    if not zipfile.is_zipfile(zip_path):
        raise BackupError("This is not a SMOS backup file (not a zip archive).")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if sorted(names) != sorted(MEMBERS):
                raise BackupError("This is not a SMOS backup: it must hold exactly manifest.json, metadata.db and faiss.index.")
            if zf.getinfo("manifest.json").file_size > _MAX_MANIFEST_BYTES:
                raise BackupError("The backup manifest is unreasonably large.")
            try:
                manifest = json.loads(zf.read("manifest.json"))
                wanted = manifest["sha256"]
                expected_counts = manifest["counts"]
            except (ValueError, KeyError, TypeError):
                raise BackupError("The backup manifest is damaged.") from None
            if manifest.get("format") != FORMAT:
                raise BackupError(f"Unsupported backup format {manifest.get('format')!r}; this SMOS reads format {FORMAT}.")
            for name in MEMBERS[1:]:  # extracted under fixed names, never under an archive path
                digest = hashlib.sha256()
                with zf.open(name) as source, open(workdir / name, "wb") as target:
                    for block in iter(lambda: source.read(_CHUNK), b""):
                        digest.update(block)
                        target.write(block)
                if wanted.get(name) != digest.hexdigest():
                    raise BackupError(f"The backup is damaged: {name} does not match its checksum.")
    except (zipfile.BadZipFile, EOFError, OSError, zlib.error) as err:
        raise BackupError(f"The backup file is damaged: {err}") from None

    db_path, index_path = workdir / "metadata.db", workdir / "faiss.index"
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            run_migrations(conn, STORE_MIGRATIONS, db_path)  # migrates an older backup; refuses a newer one
        except SchemaTooNewError as err:
            raise BackupError(f"This backup was made by a newer SMOS: {err}") from None
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("The backup database failed its integrity check.")
        memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    except sqlite3.DatabaseError as err:
        raise BackupError(f"The backup database is not readable: {err}") from None
    finally:
        conn.close()
    try:
        vectors = faiss.read_index(str(index_path)).ntotal
    except Exception as err:  # noqa: BLE001 - faiss raises plain RuntimeError
        raise BackupError(f"The backup search index is not readable: {err}") from None
    if vectors != memories:
        raise BackupError(f"The backup is inconsistent: {memories} stored items but {vectors} index entries.")
    if expected_counts != {"memories": memories, "vectors": vectors}:
        raise BackupError("The backup manifest does not match its contents.")
    return manifest


# ---------------------------------------------------------------------- loading a backup
SAFETY_DIR = "store-safety"
SWAP_MARKER = "store-swap.json"
_LIVE_FILES = ("metadata.db", "metadata.db-wal", "metadata.db-shm", "faiss.index")


def _move_files(source: Path, target: Path) -> None:
    for name in _LIVE_FILES:
        if (source / name).exists():
            os.replace(source / name, target / name)


def _restore_safety(data_dir: Path) -> None:
    """Put the safety copy back as the live store, discarding whatever half-loaded files are there."""
    safety = data_dir / SAFETY_DIR
    for name in _LIVE_FILES:
        (data_dir / name).unlink(missing_ok=True)
    _move_files(safety, data_dir)
    (data_dir / SWAP_MARKER).unlink(missing_ok=True)
    shutil.rmtree(safety, ignore_errors=True)


def recover_interrupted_swap(data_dir: Path) -> None:
    """Run at startup, before the store is opened. A swap marker means SMOS stopped in the middle of
    loading a backup: the safety copy, if it exists, becomes the live store again."""
    data_dir = Path(data_dir)
    marker = data_dir / SWAP_MARKER
    if not marker.exists():
        return
    if (data_dir / SAFETY_DIR).is_dir():
        log.warning("an interrupted backup load was found: restoring the previous store")
        _restore_safety(data_dir)
    else:
        marker.unlink(missing_ok=True)


def load_backup(app, upload_path: Path) -> dict:
    """Replace the live store with the backup in `upload_path`. Any problem found before the swap
    leaves the live store untouched; a problem after it restores the previous store. Returns the manifest."""
    if app.state.pentest:
        raise StoreOpRefused("Loading a backup is off while pentest mode is on.")
    data_dir = Path(app.state.live_dir)
    work = Path(tempfile.mkdtemp(prefix="load-tmp-", dir=data_dir))
    try:
        manifest = validate_backup(upload_path, work)
        with paused_api(app):
            _swap_in(app, data_dir, work, manifest)
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _swap_in(app, data_dir: Path, work: Path, manifest: dict) -> None:
    safety, marker = data_dir / SAFETY_DIR, data_dir / SWAP_MARKER
    shutil.rmtree(safety, ignore_errors=True)
    safety.mkdir()
    marker.write_text(json.dumps({"safety": SAFETY_DIR, "at": datetime.now(timezone.utc).isoformat()}),
                      encoding="utf-8")
    app.state.store.close()
    try:
        _move_files(data_dir, safety)
        for name in MEMBERS[1:]:
            os.replace(work / name, data_dir / name)
        loaded = open_store(data_dir)
        try:
            loaded.health_check()
            stats = loaded.stats()
            if stats["memories"] != manifest["counts"]["memories"]:
                raise RuntimeError("the loaded store does not hold the number of items the backup lists")
            loaded.query("backup verification query", k=1)
        except BaseException:
            loaded.close()
            raise
    except BaseException as err:
        _restore_safety(data_dir)
        app.state.store = open_store(data_dir)
        log.exception("loading a backup failed; the previous store was restored")
        raise BackupError(f"The backup could not be loaded; the previous store was restored. Reason: {err}") from None
    app.state.store = loaded
    marker.unlink(missing_ok=True)  # marker first: a crash now leaves only an orphan safety folder
    shutil.rmtree(safety, ignore_errors=True)
    log.warning("backup loaded (%d items); the safety copy was deleted", manifest["counts"]["memories"])
