from __future__ import annotations

import atexit
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import faiss
import numpy as np

from smos.memory.bm25_index import BM25Index
from smos.memory.embeddings import embed, embed_batch
from smos.memory.entity_extractor import extract_entities
from smos.memory.graph_store import GraphStore
from smos.memory.schemas import MemoryObject
from smos.storage.migrations import STORE_MIGRATIONS, run_migrations

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
        self._doc_locks: dict[str, list] = {}  # doc_id -> [lock, holders+waiters]
        self._doc_locks_guard = Lock()

        self._index: faiss.IndexIDMap = self._load_or_create_index()
        self._db: sqlite3.Connection = self._init_db()
        self._rebuild_index_if_stale()
        self.bm25 = BM25Index()
        self._seed_bm25()
        self.graph = GraphStore(self._db)
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
        run_migrations(conn, STORE_MIGRATIONS, self._db_file)
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

    def _seed_bm25(self) -> None:
        """Build BM25 index from all memories in SQLite."""
        rows = self._db.execute(
            "SELECT uuid, content FROM memories ORDER BY row_id"
        ).fetchall()
        self.bm25.build([(r[0], r[1]) for r in rows])

    def _update_graph(self, memory_id: str, content: str) -> None:
        """Extract entities from content and wire them into the graph.

        Best-effort, additive only. Does not seed from pre-existing memories
        on startup — only new store()/store_batch() calls populate the graph
        (see docs/HANDOFF.md for the backfill caveat).
        """
        self.graph.add_node(memory_id, "memory", memory_id[:8])
        for entity in extract_entities(content):
            entity_id = f"entity:{entity.name}"
            self.graph.add_node(entity_id, "entity", entity.name, metadata={"kind": entity.kind})
            self.graph.add_edge(memory_id, entity_id, "references")

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
                "INSERT INTO memories (uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                "doc_id, ordinal, char_start, char_end) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.timestamp.isoformat(),
                    ",".join(memory.tags),
                    memory.tier,
                    ",".join(memory.source_ids),
                    memory.confidence,
                    memory.doc_id,
                    memory.ordinal,
                    memory.char_start,
                    memory.char_end,
                ),
            )
            self._db.commit()
            row_id = cursor.lastrowid

            self._index.add_with_ids(vector, np.array([row_id], dtype=np.int64))
            self.bm25.update(memory.id, memory.content)
            self._update_graph(memory.id, memory.content)
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
                    "SELECT uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                    "doc_id, ordinal, char_start, char_end "
                    "FROM memories WHERE row_id = ?",
                    (int(row_id),),
                ).fetchone()
                if row is None:
                    continue
                (uuid_, type_, content, timestamp, tags, tier, source_ids, confidence,
                 doc_id, ordinal, char_start, char_end) = row

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
                        "source_ids": [s for s in source_ids.split(",") if s] if source_ids else [],
                        "confidence": confidence,
                        "doc_id": doc_id,
                        "ordinal": ordinal,
                        "char_start": char_start,
                        "char_end": char_end,
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
            self._db.execute("DELETE FROM summaries WHERE node_id = ?", (uuid,))
            self._db.commit()
            self.bm25.remove(uuid)
            self.graph.remove_node(uuid)
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
                self._db.execute("DELETE FROM summaries WHERE node_id = ?", (uuid,))
            self.bm25.remove_batch(uuids)
            for uuid_to_unlink in uuids:
                self.graph.remove_node(uuid_to_unlink)
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

    def bump_recall(self, uuids: list[str]) -> None:
        if not uuids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.executemany(
                "UPDATE memories SET recall_count = recall_count + 1, last_recalled = ? WHERE uuid = ?",
                [(now, uuid) for uuid in uuids],
            )
            self._db.commit()

    def recompute_tiers_by_frequency(self) -> None:
        now = datetime.now(timezone.utc)
        hot_cutoff = (now - timedelta(days=7)).isoformat()
        warm_cutoff = (now - timedelta(days=30)).isoformat()
        with self._lock:
            self._db.execute(
                """
                UPDATE memories SET tier = CASE
                    WHEN last_recalled >= ? OR recall_count > 10 THEN 'hot'
                    WHEN last_recalled >= ? OR recall_count > 3 THEN 'warm'
                    ELSE 'cold'
                END
                """,
                (hot_cutoff, warm_cutoff),
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
                "SELECT uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                "doc_id, ordinal, char_start, char_end "
                "FROM memories WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        (uuid_, type_, content, timestamp, tags, tier, source_ids, confidence,
         doc_id, ordinal, char_start, char_end) = row
        return {
            "id": uuid_,
            "content": content,
            "metadata": {
                "type": type_,
                "timestamp": timestamp,
                "tags": tags,
                "tier": tier,
                "source_ids": [s for s in source_ids.split(",") if s] if source_ids else [],
                "confidence": confidence,
                "doc_id": doc_id,
                "ordinal": ordinal,
                "char_start": char_start,
                "char_end": char_end,
            },
        }

    @contextmanager
    def document_lock(self, doc_id: str):
        """Serialize work on one document id (concurrent ingests of the same document
        run one after the other). The lock entry is reference-counted and removed when
        the last holder or waiter leaves, so the map never outgrows the work in flight."""
        with self._doc_locks_guard:
            entry = self._doc_locks.setdefault(doc_id, [Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._doc_locks_guard:
                entry[1] -= 1
                if entry[1] == 0:
                    del self._doc_locks[doc_id]

    def set_summary(self, node_id: str, summary: str, source: str = "") -> None:
        """Attach caller-supplied summary text to any node id (memory uuid,
        document or section). SMOS never generates summaries itself."""
        with self._lock:
            self._db.execute(
                "INSERT INTO summaries (node_id, summary, source, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET summary=excluded.summary, "
                "source=excluded.source, updated_at=excluded.updated_at",
                (node_id, summary, source, datetime.utcnow().isoformat()),
            )
            self._db.commit()

    def delete_summary(self, node_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM summaries WHERE node_id = ?", (node_id,))
            self._db.commit()

    def get_summaries(self, node_ids: list[str]) -> dict[str, dict]:
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        with self._lock:
            rows = self._db.execute(
                f"SELECT node_id, summary, source FROM summaries WHERE node_id IN ({placeholders})",
                list(node_ids),
            ).fetchall()
        return {nid: {"summary": s, "source": src} for nid, s, src in rows}

    def get_by_timestamp_range(self, since: str = "", until: str = "") -> list[dict]:
        sql = "SELECT uuid, content FROM memories WHERE 1=1"
        params: list[str] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    def stats(self) -> dict:
        """Cheap counts for the dashboard: documents (placeholders excluded), segments, all memories."""
        with self._lock:
            documents = self._db.execute(
                "SELECT COUNT(*) FROM graph_nodes WHERE type = 'document' "
                "AND COALESCE(json_extract(metadata, '$.placeholder'), 0) = 0"
            ).fetchone()[0]
            segments = self._db.execute(
                "SELECT COUNT(*) FROM memories WHERE type = 'segment'"
            ).fetchone()[0]
            memories = self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return {"documents": documents, "segments": segments, "memories": memories}

    def file_sizes(self) -> dict:
        """On-disk bytes of the metadata database (with its WAL) and the search index."""

        def size(path: Path) -> int:
            return path.stat().st_size if path.exists() else 0

        wal = self._db_file.with_name(self._db_file.name + "-wal")
        return {"store_db": size(self._db_file) + size(wal), "index": size(self._index_file)}

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
                        "INSERT INTO memories (uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                        "doc_id, ordinal, char_start, char_end) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            memory.id,
                            memory.type,
                            memory.content,
                            memory.timestamp.isoformat(),
                            ",".join(memory.tags),
                            memory.tier,
                            ",".join(memory.source_ids),
                            memory.confidence,
                            memory.doc_id,
                            memory.ordinal,
                            memory.char_start,
                            memory.char_end,
                        ),
                    )
                    row_ids.append(cursor.lastrowid)
                self._db.commit()

                id_arr = np.array(row_ids, dtype=np.int64)
                self._index.add_with_ids(arr, id_arr)
                self.bm25.add_batch([(m.id, m.content) for m in chunk])
                for memory in chunk:
                    self._update_graph(memory.id, memory.content)
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

    def save_quarantine(
        self, key: str, system: str, native_id: str, raw_payload: str, errors: str
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO ingest_quarantine (key, system, native_id, raw_payload, errors, quarantined_at) "
                "VALUES (?,?,?,?,?,?)",
                (key, system, native_id, raw_payload, errors, ts),
            )
            self._db.commit()

    def get_quarantine(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT key, system, native_id, raw_payload, errors, quarantined_at "
                "FROM ingest_quarantine WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "key": row[0], "system": row[1], "native_id": row[2],
            "raw_payload": row[3], "errors": row[4], "quarantined_at": row[5],
        }

    def get_segment_ids_by_doc(self, doc_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT uuid FROM memories WHERE doc_id = ?", (doc_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def get_segment_by_ordinal(self, doc_id: str, ordinal: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid FROM memories WHERE doc_id = ? AND ordinal = ?",
                (doc_id, ordinal),
            ).fetchone()
        if row is None:
            return None
        return self.get_by_uuid(row[0])

    def get_checkpoint(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT doc_id, content_hash, total_leaves, next_leaf_index, segment_ids, status, updated_at "
                "FROM ingest_checkpoints WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "doc_id": row[0],
            "content_hash": row[1],
            "total_leaves": row[2],
            "next_leaf_index": row[3],
            "segment_ids": [s for s in row[4].split(",") if s] if row[4] else [],
            "status": row[5],
            "updated_at": row[6],
        }

    def save_checkpoint(
        self,
        doc_id: str,
        content_hash: str,
        total_leaves: int,
        next_leaf_index: int,
        segment_ids: list[str],
        status: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO ingest_checkpoints "
                "(doc_id, content_hash, total_leaves, next_leaf_index, segment_ids, status, updated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                "content_hash=excluded.content_hash, total_leaves=excluded.total_leaves, "
                "next_leaf_index=excluded.next_leaf_index, segment_ids=excluded.segment_ids, "
                "status=excluded.status, updated_at=excluded.updated_at",
                (doc_id, content_hash, total_leaves, next_leaf_index, ",".join(segment_ids), status, ts),
            )
            self._db.commit()

    def delete_checkpoint(self, doc_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM ingest_checkpoints WHERE doc_id = ?", (doc_id,))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._save_index()
            self._db.close()
