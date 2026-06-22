from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smos.memory.vector_store import VectorStore

_SIMILARITY_THRESHOLD = 0.12
_MIN_STORE_SIZE = 10


class LifecycleManager:
    """Background lifecycle manager: tier promotion + incremental deduplication."""

    def __init__(self, store: "VectorStore") -> None:
        self._store = store
        self._running = threading.Lock()

    def on_insert(self, insert_count: int) -> None:
        threading.Thread(
            target=self._run_cycle, daemon=True, name="lifecycle"
        ).start()

    def _run_cycle(self) -> None:
        if not self._running.acquire(blocking=False):
            return
        try:
            self._promote_tiers()
            self._deduplicate()
        except Exception:
            pass
        finally:
            self._running.release()

    def _promote_tiers(self) -> None:
        uuids = self._store.get_all_uuids()
        n = len(uuids)
        if n < _MIN_STORE_SIZE:
            return
        cold_cut = n // 4
        warm_cut = n // 2
        updates = (
            [(uuid, "cold") for uuid in uuids[:cold_cut]]
            + [(uuid, "warm") for uuid in uuids[cold_cut:warm_cut]]
        )
        self._store.update_tiers_batch(updates)

    def _deduplicate(self) -> None:
        """Incremental dedup: O(M) per cycle where M = inserts since last drain."""
        new_uuids = self._store._drain_new_ids()
        if len(new_uuids) < 2:
            return
        if self._store.count() < _MIN_STORE_SIZE:
            return

        new_set = set(new_uuids)
        processed: set[str] = set()
        to_delete: list[str] = []
        to_delete_set: set[str] = set()

        for uuid in new_uuids:
            if uuid in to_delete_set:
                processed.add(uuid)
                continue

            record = self._store.get_by_uuid(uuid)
            if record is None:
                processed.add(uuid)
                continue

            similar = self._store.query(record["content"], k=5)
            for s in similar:
                if s["distance"] >= _SIMILARITY_THRESHOLD:
                    continue
                sid = s["id"]
                if sid == uuid or sid in to_delete_set:
                    continue
                if sid not in new_set or sid in processed:
                    to_delete.append(uuid)
                    to_delete_set.add(uuid)
                    break

            processed.add(uuid)

        if to_delete:
            self._store.delete_batch(to_delete)
