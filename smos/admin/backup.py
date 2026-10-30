"""Store backup files: create one, and check one before it may replace the live store.

A backup is a zip of exactly three members: manifest.json, metadata.db and faiss.index. It never
holds admin.db (tokens, invitations, usage log, admin accounts).
"""
from __future__ import annotations

import hashlib
import json
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
from smos.admin.storeops import StoreOpRefused, paused_api
from smos.storage.migrations import STORE_MIGRATIONS, SchemaTooNewError, run_migrations

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
