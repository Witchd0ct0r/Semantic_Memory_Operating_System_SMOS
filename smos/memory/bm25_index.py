from __future__ import annotations

import math
import re
import threading
from collections import Counter

import numpy as np

# Okapi BM25 constants, the values rank_bm25.BM25Okapi used (its outputs are what this class must reproduce).
_K1 = 1.5
_B = 0.75
_EPSILON = 0.25


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25Index:
    """In-memory BM25 index over stored memories. Thread-safe.

    An inverted index: scoring a query touches only the documents that contain its words. Scores, order and
    tie-breaks are identical to the rank_bm25 implementation this replaced (tests/bm25_reference.py keeps that
    implementation as the oracle): score descending, ties by insertion order newest first, zero-score documents
    padding the result newest first.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uuids: list[str] = []
        self._uuid_set: set[str] = set()
        self._counts: list[Counter] = []
        self._reset_derived()

    def _reset_derived(self) -> None:
        self._doc_len = np.zeros(0, dtype=np.float64)
        self._avgdl = 0.0
        self._postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._idf: dict[str, float] = {}

    def _rebuild(self) -> None:
        """Recompute the postings and weights from the retained per-document word counts."""
        n = len(self._uuids)
        if n == 0:
            self._reset_derived()
            return
        lengths = [sum(counts.values()) for counts in self._counts]
        self._doc_len = np.array(lengths, dtype=np.float64)
        self._avgdl = sum(lengths) / n
        slots: dict[str, list[int]] = {}
        freqs: dict[str, list[int]] = {}
        for slot, counts in enumerate(self._counts):
            for word, freq in counts.items():
                slots.setdefault(word, []).append(slot)
                freqs.setdefault(word, []).append(freq)
        self._postings = {
            word: (np.array(slots[word], dtype=np.int64), np.array(freqs[word], dtype=np.float64)) for word in slots
        }
        idf_sum = 0.0
        idf: dict[str, float] = {}
        negative: list[str] = []
        for word, word_slots in slots.items():
            value = math.log(n - len(word_slots) + 0.5) - math.log(len(word_slots) + 0.5)
            idf[word] = value
            idf_sum += value
            if value < 0:
                negative.append(word)
        floor = _EPSILON * (idf_sum / len(idf))
        for word in negative:
            idf[word] = floor
        self._idf = idf

    def build(self, corpus: list[tuple[str, str]]) -> None:
        """Build index from (uuid, content) pairs. Replaces existing index."""
        with self._lock:
            self._uuids = [uuid for uuid, _ in corpus]
            self._uuid_set = set(self._uuids)
            self._counts = [Counter(_tokenize(content)) for _, content in corpus]
            self._rebuild()

    def update(self, uuid: str, content: str) -> None:
        """Add a single document (ignored when the uuid is already present)."""
        with self._lock:
            if uuid in self._uuid_set:
                return
            self._uuids.append(uuid)
            self._uuid_set.add(uuid)
            self._counts.append(Counter(_tokenize(content)))
            self._rebuild()

    def add_batch(self, docs: list[tuple[str, str]]) -> None:
        """Add multiple (uuid, content) pairs; uuids already present are skipped."""
        with self._lock:
            added = False
            for doc_uuid, content in docs:
                if doc_uuid in self._uuid_set:
                    continue
                self._uuids.append(doc_uuid)
                self._uuid_set.add(doc_uuid)
                self._counts.append(Counter(_tokenize(content)))
                added = True
            if added:
                self._rebuild()

    def remove(self, uuid: str) -> None:
        """Remove a document by uuid (unknown uuids are ignored)."""
        self.remove_batch([uuid])

    def remove_batch(self, uuids: list[str]) -> None:
        """Remove multiple documents by uuid; uuids not present are skipped."""
        with self._lock:
            doomed = set(uuids) & self._uuid_set
            if not doomed:
                return
            keep = [i for i, uuid in enumerate(self._uuids) if uuid not in doomed]
            self._uuids = [self._uuids[i] for i in keep]
            self._counts = [self._counts[i] for i in keep]
            self._uuid_set -= doomed
            self._rebuild()

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Return top-k (uuid, score) pairs for the query text.

        Note: Results may include zero-score entries (insertion-order tie-broken) when no query terms match the corpus.
        Callers should not assume all returned entries are relevant matches."""
        with self._lock:
            n = len(self._uuids)
            if n == 0 or k <= 0:
                return []
            scores = np.zeros(n, dtype=np.float64)
            has_negative = False
            if self._avgdl > 0:
                for word in _tokenize(text):
                    posting = self._postings.get(word)
                    if posting is None:
                        continue
                    idf = self._idf[word]
                    if idf == 0:
                        continue
                    has_negative = has_negative or idf < 0
                    slots, freq = posting
                    # same operation order as rank_bm25, so equal inputs give equal floats
                    scores[slots] += idf * (freq * (_K1 + 1) / (freq + _K1 * (1 - _B + _B * self._doc_len[slots] / self._avgdl)))
            chosen = self._top(scores, k, has_negative)
            return [(self._uuids[slot], float(scores[slot])) for slot in chosen]

    @staticmethod
    def _top(scores: np.ndarray, k: int, has_negative: bool) -> np.ndarray:
        """Slots of the best k documents: score descending, ties newest (highest slot) first."""
        slots = np.arange(len(scores))
        if has_negative:  # scores can be negative or zero: rank every document
            return np.lexsort((-slots, -scores))[:k]
        positive = np.flatnonzero(scores > 0)
        if len(positive) > k:
            values = scores[positive]
            kth_largest = np.partition(values, len(values) - k)[len(values) - k]
            positive = positive[values >= kth_largest]  # keeps everything tied with the k-th score
        ranked = positive[np.lexsort((-positive, -scores[positive]))][:k]
        if len(ranked) < k:
            zeros = np.flatnonzero(scores == 0)[::-1][: k - len(ranked)]
            ranked = np.concatenate([ranked, zeros])
        return ranked
