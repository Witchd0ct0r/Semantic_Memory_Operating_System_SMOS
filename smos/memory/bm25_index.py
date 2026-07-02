from __future__ import annotations

import re
import threading
from typing import Optional

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25Index:
    """In-memory BM25 index over stored memories. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uuids: list[str] = []
        self._tokenized: list[list[str]] = []
        self._bm25: Optional[BM25Okapi] = None

    def build(self, corpus: list[tuple[str, str]]) -> None:
        """Build index from (uuid, content) pairs. Replaces existing index."""
        with self._lock:
            if not corpus:
                self._uuids = []
                self._tokenized = []
                self._bm25 = None
                return
            self._uuids = [uuid for uuid, _ in corpus]
            self._tokenized = [_tokenize(content) for _, content in corpus]
            self._bm25 = BM25Okapi(self._tokenized)

    def update(self, uuid: str, content: str) -> None:
        """Add a single document. Rebuilds the BM25 index."""
        with self._lock:
            if uuid in self._uuids:
                return
            tokens = _tokenize(content)
            self._uuids.append(uuid)
            self._tokenized.append(tokens)
            # Rebuild: BM25Okapi has no incremental add; rebuild is cheap at <100K docs
            self._bm25 = BM25Okapi(self._tokenized)

    def remove(self, uuid: str) -> None:
        """Remove a document by uuid. Rebuilds index."""
        with self._lock:
            if uuid not in self._uuids:
                return
            idx = self._uuids.index(uuid)
            self._uuids.pop(idx)
            self._tokenized.pop(idx)
            self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Return top-k (uuid, score) pairs for the query text."""
        with self._lock:
            if self._bm25 is None or not self._uuids:
                return []
            tokens = _tokenize(text)
            scores = self._bm25.get_scores(tokens)
            ranked = sorted(
                enumerate(zip(self._uuids, scores.tolist())),
                key=lambda x: (-x[1][1], -x[0]),  # Sort by score desc, then by insertion order desc
            )
            return [(uuid, float(score)) for _, (uuid, score) in ranked[:k]]
