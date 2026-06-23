from __future__ import annotations

import atexit
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import faiss
import numpy as np

from smos.memory.embeddings import embed, embed_batch
from smos.memory.schemas import MemoryObject

_DEFAULT_DB_PATH = Path.home() / ".smos" / "data"
_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension
_REBUILD_BATCH = 256  # rows per embed batch during crash-recovery rebuild


class VectorStore:
    """Thread-safe persistent vector store (FAISS cosine similarity + SQLite metadata)."""

    def __init__(
        self,
        persist_path: Optional[Path] = None,
        lifecycle_callback: Optional[Callable[[int], None]] = None,
        save_interval: int = 50,
    ) -> None:
        self._path = persist_path or _DEFAULT_DB_PATH
        self._path.mkdir(parents=True, exist_ok=True)
        self._index_file = self._path / "faiss.index"
        self._db_file = self._path / "metadata.db"
        self._lock = Lock()
        self._new_ids_lock = Lock()
        self._lifecycle_callback = lifecycle_callback
        self._insert_count = 0
        self._save_interval = save_interval
        self._new_ids: list[str] = []

        self._index: faiss.IndexIDMap = self._load_or_create_index()
        self._db: sqlite3.Connection = self._init_db()
        self._rebuild_index_if_stale()
        atexit.register(self._atexit_save)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_or_create_index(self) -> faiss.IndexIDMap:
        flat = faiss.IndexFlatIP(_EMBEDDING_DIM)
        id_map = faiss.IndexIDMap(flat)
        if self._index_file.exists():
            try:
                id_map = faiss.read_index(str(self._index_file))
            except Exception:
                pass  # corrupted index — _rebuild_index_if_stale will recover
        return id_map

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_file), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                row_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid      TEXT UNIQUE NOT NULL,
                type      TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                tags      TEXT NOT NULL DEFAULT '',
                tier      TEXT NOT NULL DEFAULT 'hot'
            )
        """)
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN tier TEXT NOT NULL DEFAULT 'hot'")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verbatim (
                key       TEXT PRIMARY KEY,
                content   TEXT NOT NULL,
                label     TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingested_files (
                path        TEXT PRIMARY KEY,
                memory_id   TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            )
        """)
        conn.commit()
        return conn

    def _rebuild_index_if_stale(self) -> None:
        """Re-embed all SQLite rows into a fresh FAISS index when counts diverge."""
        db_count = self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if self._index.ntotal == db_count:
            return
        flat = faiss.IndexFlatIP(_EMBEDDING_DIM)
        self._index = faiss.IndexIDMap(flat)
        rows = self._db.execute(
            "SELECT row_id, content FROM memories ORDER BY row_id"
        ).fetchall()
        if not rows:
            return
        row_ids = np.array([r[0] for r in rows], dtype=np.int64)
        contents = [r[1] for r in rows]
        all_vecs: list[list[float]] = []
        for i in range(0, len(contents), _REBUILD_BATCH):
            all_vecs.extend(embed_batch(contents[i : i + _REBUILD_BATCH]))
        arr = np.array(all_vecs, dtype=np.float32)
        faiss.normalize_L2(arr)
        self._index.add_with_ids(arr, row_ids)
        self._save_index()

    def _save_index(self) -> None:
        faiss.write_index(self._index, str(self._index_file))

    def _atexit_save(self) -> None:
        try:
            with self._lock:
                self._save_index()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, memory: MemoryObject) -> str:
        with self._lock:
            vector = np.array([embed(memory.content)], dtype=np.float32)
            faiss.normalize_L2(vector)

            cursor = self._db.execute(
                "INSERT INTO memories (uuid, type, content, timestamp, tags, tier) "
                "VALUES (?,?,?,?,?,?)",
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.timestamp.isoformat(),
                    ",".join(memory.tags),
                    memory.tier,
                ),
            )
            self._db.commit()
            row_id = cursor.lastrowid

            self._index.add_with_ids(vector, np.array([row_id], dtype=np.int64))
            self._insert_count += 1
            count = self._insert_count
            if count % self._save_interval == 0:
                self._save_index()

        with self._new_ids_lock:
            self._new_ids.append(memory.id)

        if self._lifecycle_callback and count % 50 == 0:
            self._lifecycle_callback(count)

        return memory.id

    def _drain_new_ids(self) -> list[str]:
        with self._new_ids_lock:
            ids = self._new_ids[:]
            self._new_ids.clear()
        return ids

    def query(self, query_text: str, k: int = 5) -> list[dict]:
        return self._query_internal(query_text, k, domain_tags=None)

    def query_domain(
        self,
        query_text: str,
        k: int = 20,
        domain_tags: Optional[list[str]] = None,
    ) -> list[dict]:
        return self._query_internal(query_text, k, domain_tags=domain_tags)

    def _query_internal(
        self,
        query_text: str,
        k: int,
        domain_tags: Optional[list[str]],
    ) -> list[dict]:
        with self._lock:
            total = self._index.ntotal
            if total == 0:
                return []

            vector = np.array([embed(query_text)], dtype=np.float32)
            faiss.normalize_L2(vector)

            n = min(k, total)
            scores, ids = self._index.search(vector, n)

            results: list[dict] = []
            for score, row_id in zip(scores[0], ids[0]):
                if row_id == -1:
                    continue
                row = self._db.execute(
                    "SELECT uuid, type, content, timestamp, tags, tier "
                    "FROM memories WHERE row_id = ?",
                    (int(row_id),),
                ).fetchone()
                if row is None:
                    continue
                uuid_, type_, content, timestamp, tags, tier = row

                if domain_tags:
                    tag_set = {t.strip() for t in tags.split(",") if t.strip()}
                    if not tag_set.intersection(domain_tags):
                        continue

                results.append({
                    "id": uuid_,
                    "content": content,
                    "metadata": {
                        "type": type_,
                        "timestamp": timestamp,
                        "tags": tags,
                        "tier": tier,
                    },
                    "distance": float(1.0 - score),
                    "score": float(score),
                })
        return results

    def delete(self, uuid: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT row_id FROM memories WHERE uuid = ?", (uuid,)
            ).fetchone()
            if row is None:
                return False
            row_id = int(row[0])
            self._index.remove_ids(np.array([row_id], dtype=np.int64))
            self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            self._db.commit()
            self._save_index()
        return True

    def delete_batch(self, uuids: list[str]) -> int:
        if not uuids:
            return 0
        with self._lock:
            row_ids: list[int] = []
            for uuid in uuids:
                row = self._db.execute(
                    "SELECT row_id FROM memories WHERE uuid = ?", (uuid,)
                ).fetchone()
                if row is None:
                    continue
                row_ids.append(int(row[0]))
                self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            if row_ids:
                self._index.remove_ids(np.array(row_ids, dtype=np.int64))
                self._db.commit()
                self._save_index()
        return len(row_ids)

    def update_tier(self, uuid: str, tier: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE memories SET tier = ? WHERE uuid = ?", (tier, uuid)
            )
            self._db.commit()

    def update_tiers_batch(self, updates: list[tuple[str, str]]) -> None:
        if not updates:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE memories SET tier = ? WHERE uuid = ?",
                [(tier, uuid) for uuid, tier in updates],
            )
            self._db.commit()

    def get_all_uuids(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT uuid FROM memories ORDER BY row_id"
            ).fetchall()
        return [r[0] for r in rows]

    def get_by_uuid(self, uuid: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid, type, content, timestamp, tags, tier "
                "FROM memories WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        uuid_, type_, content, timestamp, tags, tier = row
        return {
            "id": uuid_,
            "content": content,
            "metadata": {
                "type": type_,
                "timestamp": timestamp,
                "tags": tags,
                "tier": tier,
            },
        }

    def count(self) -> int:
        with self._lock:
            return self._index.ntotal

    def store_verbatim(self, content: str, label: str = "") -> str:
        """Store content without compression or embedding. Returns the retrieval key."""
        key = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO verbatim (key, content, label, timestamp) VALUES (?,?,?,?)",
                (key, content, label, ts),
            )
            self._db.commit()
        return key

    def retrieve_verbatim(self, key: str) -> Optional[dict]:
        """Return the exact content stored under key, or None if not found."""
        with self._lock:
            row = self._db.execute(
                "SELECT key, content, label, timestamp FROM verbatim WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {"key": row[0], "content": row[1], "label": row[2], "timestamp": row[3]}

    def store_batch(
        self,
        memories: list[MemoryObject],
        batch_size: int = 256,
    ) -> list[str]:
        """Batch-store memories with a single embedding call per batch.

        Embeds outside the lock (fixes the known lock-contention issue for the write path).
        Uses one SQLite commit per batch chunk instead of N individual transactions.
        """
        if not memories:
            return []

        all_ids: list[str] = []

        for start in range(0, len(memories), batch_size):
            chunk = memories[start : start + batch_size]

            # Embed outside the lock — CPU-bound and safe to do concurrently with reads
            vecs = embed_batch([m.content for m in chunk])
            arr = np.array(vecs, dtype=np.float32)
            faiss.normalize_L2(arr)

            with self._lock:
                row_ids: list[int] = []
                for memory in chunk:
                    cursor = self._db.execute(
                        "INSERT INTO memories (uuid, type, content, timestamp, tags, tier) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            memory.id,
                            memory.type,
                            memory.content,
                            memory.timestamp.isoformat(),
                            ",".join(memory.tags),
                            memory.tier,
                        ),
                    )
                    row_ids.append(cursor.lastrowid)
                self._db.commit()

                id_arr = np.array(row_ids, dtype=np.int64)
                self._index.add_with_ids(arr, id_arr)
                self._insert_count += len(chunk)

            with self._new_ids_lock:
                self._new_ids.extend(m.id for m in chunk)

            all_ids.extend(m.id for m in chunk)

        with self._lock:
            self._save_index()

        if self._lifecycle_callback:
            self._lifecycle_callback(self._insert_count)

        return all_ids

    # ------------------------------------------------------------------
    # File-ingestion tracking helpers
    # ------------------------------------------------------------------

    def get_ingested_paths(self) -> set[str]:
        """Return the set of file paths already ingested into this store."""
        with self._lock:
            rows = self._db.execute(
                "SELECT path FROM ingested_files"
            ).fetchall()
        return {r[0] for r in rows}

    def mark_files_ingested_batch(
        self, path_id_pairs: list[tuple[str, str]]
    ) -> None:
        """Record that a batch of files has been ingested."""
        if not path_id_pairs:
            return
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO ingested_files (path, memory_id, ingested_at) "
                "VALUES (?,?,?)",
                [(p, mid, ts) for p, mid in path_id_pairs],
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._save_index()
            self._db.close()
