from __future__ import annotations

import re
import threading
from typing import Optional

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class ReferenceBM25Index:
    """The pre-0.3.4 rank_bm25 implementation, kept verbatim as the oracle for the numpy index."""

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
            # Known limitation: this full rebuild is O(n) per call, so single-item
            # write paths (store()/delete()) are O(n^2) cumulative at scale. It runs
            # under VectorStore._lock, so write latency grows with corpus size.
            # Tracked for a future incremental-index phase (see roadmap).
            self._bm25 = BM25Okapi(self._tokenized)

    def add_batch(self, docs: list[tuple[str, str]]) -> None:
        """Add multiple (uuid, content) pairs, rebuilding the BM25 index exactly once.

        Skips uuids already present (same add-if-absent semantics as update()).
        Avoids the O(n^2) rebuild cost of calling update() once per document.
        """
        with self._lock:
            if not docs:
                return
            added = False
            for doc_uuid, content in docs:
                if doc_uuid in self._uuids:
                    continue
                self._uuids.append(doc_uuid)
                self._tokenized.append(_tokenize(content))
                added = True
            if added:
                self._bm25 = BM25Okapi(self._tokenized)

    def remove(self, uuid: str) -> None:
        """Remove a document by uuid. Rebuilds index."""
        with self._lock:
            if uuid not in self._uuids:
                return
            idx = self._uuids.index(uuid)
            self._uuids.pop(idx)
            self._tokenized.pop(idx)
            # Known limitation: this full rebuild is O(n) per call, so single-item
            # write paths (store()/delete()) are O(n^2) cumulative at scale. It runs
            # under VectorStore._lock, so write latency grows with corpus size.
            # Tracked for a future incremental-index phase (see roadmap).
            self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def remove_batch(self, uuids: list[str]) -> None:
        """Remove multiple documents by uuid, rebuilding the BM25 index exactly once.

        Uuids not present in the index are silently skipped (same behavior as
        remove() for a single nonexistent uuid).
        Avoids the O(n^2) rebuild cost of calling remove() once per document.
        """
        with self._lock:
            if not uuids:
                return
            to_remove = set(uuids)
            removed = False
            kept_uuids: list[str] = []
            kept_tokenized: list[list[str]] = []
            for existing_uuid, tokens in zip(self._uuids, self._tokenized):
                if existing_uuid in to_remove:
                    removed = True
                    continue
                kept_uuids.append(existing_uuid)
                kept_tokenized.append(tokens)
            if not removed:
                return
            self._uuids = kept_uuids
            self._tokenized = kept_tokenized
            # Known limitation: this full rebuild is O(n) per call; batching at
            # least amortizes it to once per call instead of once per uuid.
            # Tracked for a future incremental-index phase (see roadmap).
            self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Return top-k (uuid, score) pairs for the query text.

        Note: Results may include zero-score entries (insertion-order tie-broken) when no query terms match the corpus.
        Callers should not assume all returned entries are relevant matches."""
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


# ---------------------------------------------------------------------------
# Helpers for the equivalence tests
# ---------------------------------------------------------------------------

import random  # noqa: E402

import pytest  # noqa: E402

_WORDS = [f"w{i}" for i in range(30)]
_WEIGHTS = [1.0 / (i + 1) for i in range(30)]  # skewed: w0 is in most documents, w29 is rare


def random_corpus(rng: random.Random, n_docs: int) -> list[tuple[str, str]]:
    corpus = []
    for i in range(n_docs):
        length = 0 if rng.random() < 0.05 else rng.randint(1, 25)
        text = " ".join(rng.choices(_WORDS, weights=_WEIGHTS, k=length))
        corpus.append((f"d{i}", text))
    return corpus


def random_query(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.05:
        return ""
    if roll < 0.10:
        return "!!! ???"
    words = [rng.choice(_WORDS) for _ in range(rng.randint(1, 4))]
    if rng.random() < 0.2:
        words.append("neverseen")
    if rng.random() < 0.2:
        words.append(words[0])
    return " ".join(words)


def assert_same_ranking(got: list[tuple[str, float]], want: list[tuple[str, float]]) -> None:
    """Same scores (float tolerance) and same order; documents whose reference scores tie may swap only inside the tie."""
    assert len(got) == len(want), f"length {len(got)} != {len(want)}"
    for (_, g), (_, w) in zip(got, want):
        assert g == pytest.approx(w, rel=1e-9, abs=1e-12)
    i = 0
    while i < len(want):
        j = i + 1
        while j < len(want) and abs(want[j][1] - want[i][1]) <= 1e-12:
            j += 1
        assert sorted(u for u, _ in got[i:j]) == sorted(u for u, _ in want[i:j]), \
            f"order differs in positions {i}:{j}: {got[i:j]} vs {want[i:j]}"
        i = j
