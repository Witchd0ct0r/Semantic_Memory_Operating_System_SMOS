from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Optional

import numpy as np

# Okapi BM25 constants, the values rank_bm25.BM25Okapi used (its outputs are what this class must reproduce).
_K1 = 1.5
_B = 0.75
_EPSILON = 0.25
_COMPACT_DEAD_SHARE = 0.25  # compact once more than this share of the slots belong to removed documents


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class _Posting:
    """The documents containing one word: slot numbers and term frequencies, appended in amortised O(1)."""

    __slots__ = ("slots", "freqs", "size")

    def __init__(self, slots: np.ndarray, freqs: np.ndarray, size: int) -> None:
        self.slots, self.freqs, self.size = slots, freqs, size

    def append(self, slot: int, freq: int) -> None:
        if self.size == len(self.slots):
            capacity = max(4, self.size * 2)
            self.slots = np.resize(self.slots, capacity)
            self.freqs = np.resize(self.freqs, capacity)
        self.slots[self.size] = slot
        self.freqs[self.size] = freq
        self.size += 1


class BM25Index:
    """In-memory BM25 index over stored memories. Thread-safe.

    An inverted index: scoring a query touches only the documents that contain its words. Scores, order and
    tie-breaks are identical to the rank_bm25 implementation this replaced (tests/bm25_reference.py keeps that
    implementation as the oracle): score descending, ties by insertion order newest first, zero-score documents
    padding the result newest first.

    Documents occupy slots in insertion order and slots are never reused, so a slot number is the insertion order.
    Removing a document marks its slot dead; the postings keep the dead slot until the index compacts itself.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._compactions = 0
        self._clear()

    def _clear(self) -> None:
        self._uuids: list[Optional[str]] = []
        self._counts: list[Optional[Counter]] = []
        self._slot_of: dict[str, int] = {}
        self._n_slots = 0
        self._live = 0
        self._dead = 0
        self._dead_idx = np.zeros(0, dtype=np.int64)
        self._total_len = 0
        self._doc_len = np.zeros(0, dtype=np.float64)
        self._alive = np.zeros(0, dtype=bool)
        self._postings: dict[str, _Posting] = {}
        self._df: dict[str, int] = {}
        self._avgdl = 0.0
        self._floor = 0.0

    # ------------------------------------------------------------------
    # Building and mutation (callers hold self._lock)
    # ------------------------------------------------------------------

    def _load(self, entries: list[tuple[str, Counter]]) -> None:
        """Replace everything with `entries` (uuid, word counts) in insertion order."""
        self._clear()
        n = len(entries)
        if n == 0:
            return
        lengths = [sum(counts.values()) for _, counts in entries]
        slots: dict[str, list[int]] = {}
        freqs: dict[str, list[int]] = {}
        for slot, (_, counts) in enumerate(entries):
            for word, freq in counts.items():
                slots.setdefault(word, []).append(slot)
                freqs.setdefault(word, []).append(freq)
        self._uuids = [uuid for uuid, _ in entries]
        self._counts = [counts for _, counts in entries]
        self._slot_of = {uuid: slot for slot, (uuid, _) in enumerate(entries)}
        self._n_slots = self._live = n
        self._total_len = sum(lengths)
        self._doc_len = np.array(lengths, dtype=np.float64)
        self._alive = np.ones(n, dtype=bool)
        for word, word_slots in slots.items():
            self._postings[word] = _Posting(np.array(word_slots, dtype=np.int32),
                                            np.array(freqs[word], dtype=np.float32), len(word_slots))
            self._df[word] = len(word_slots)
        self._refresh()

    def _append(self, uuid: str, counts: Counter) -> None:
        slot = self._n_slots
        if slot == len(self._doc_len):
            capacity = max(16, slot * 2)
            self._doc_len = np.resize(self._doc_len, capacity)
            self._alive = np.resize(self._alive, capacity)
        length = sum(counts.values())
        self._doc_len[slot] = length
        self._alive[slot] = True
        self._uuids.append(uuid)
        self._counts.append(counts)
        self._slot_of[uuid] = slot
        for word, freq in counts.items():
            posting = self._postings.get(word)
            if posting is None:
                posting = self._postings[word] = _Posting(np.zeros(4, dtype=np.int32), np.zeros(4, dtype=np.float32), 0)
            posting.append(slot, freq)
            self._df[word] = self._df.get(word, 0) + 1
        self._total_len += length
        self._n_slots += 1
        self._live += 1

    def _kill(self, uuid: str) -> None:
        slot = self._slot_of.pop(uuid)
        counts = self._counts[slot]
        for word in counts or ():
            remaining = self._df[word] - 1
            if remaining:
                self._df[word] = remaining
            else:
                del self._df[word]
                del self._postings[word]
        self._total_len -= int(self._doc_len[slot])
        self._alive[slot] = False
        self._uuids[slot] = None
        self._counts[slot] = None
        self._live -= 1
        self._dead += 1

    def _finish(self) -> None:
        """After a mutation: compact when too much is dead, then refresh the derived statistics."""
        if self._live == 0:
            self._clear()
        elif self._dead > _COMPACT_DEAD_SHARE * self._n_slots:
            self._compactions += 1
            self._load([(u, c) for u, c in zip(self._uuids, self._counts) if u is not None])
        else:
            self._dead_idx = np.flatnonzero(~self._alive[: self._n_slots])
            self._refresh()

    def _refresh(self) -> None:
        """The global statistics every score depends on: average document length and the negative-idf floor."""
        self._avgdl = self._total_len / self._live if self._live else 0.0
        if self._df:
            df = np.fromiter(self._df.values(), dtype=np.float64, count=len(self._df))
            idf = np.log(self._live - df + 0.5) - np.log(df + 0.5)
            self._floor = _EPSILON * float(idf.sum()) / len(df)
        else:
            self._floor = 0.0

    def _word_idf(self, word: str) -> float:
        df = self._df[word]
        idf = math.log(self._live - df + 0.5) - math.log(df + 0.5)
        return idf if idf >= 0 else self._floor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, corpus: list[tuple[str, str]]) -> None:
        """Build index from (uuid, content) pairs. Replaces existing index."""
        with self._lock:
            self._load([(uuid, Counter(_tokenize(content))) for uuid, content in corpus])

    def update(self, uuid: str, content: str) -> None:
        """Add a single document (ignored when the uuid is already present)."""
        self.add_batch([(uuid, content)])

    def add_batch(self, docs: list[tuple[str, str]]) -> None:
        """Add multiple (uuid, content) pairs; uuids already present are skipped."""
        with self._lock:
            added = False
            for uuid, content in docs:
                if uuid in self._slot_of:
                    continue
                self._append(uuid, Counter(_tokenize(content)))
                added = True
            if added:
                self._finish()

    def remove(self, uuid: str) -> None:
        """Remove a document by uuid (unknown uuids are ignored)."""
        self.remove_batch([uuid])

    def remove_batch(self, uuids: list[str]) -> None:
        """Remove multiple documents by uuid; uuids not present are skipped."""
        with self._lock:
            doomed = [uuid for uuid in dict.fromkeys(uuids) if uuid in self._slot_of]
            if not doomed:
                return
            for uuid in doomed:
                self._kill(uuid)
            self._finish()

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Return top-k (uuid, score) pairs for the query text.

        Note: Results may include zero-score entries (insertion-order tie-broken) when no query terms match the corpus.
        Callers should not assume all returned entries are relevant matches."""
        with self._lock:
            if self._live == 0 or k <= 0:
                return []
            n = self._n_slots
            scores = np.zeros(n, dtype=np.float64)
            has_negative = False
            if self._avgdl > 0:
                for word in _tokenize(text):
                    posting = self._postings.get(word)
                    if posting is None:
                        continue
                    idf = self._word_idf(word)
                    if idf == 0:
                        continue
                    has_negative = has_negative or idf < 0
                    slots = posting.slots[: posting.size]
                    freq = posting.freqs[: posting.size].astype(np.float64)
                    # same operation order as rank_bm25, so equal inputs give equal floats
                    scores[slots] += idf * (freq * (_K1 + 1) / (freq + _K1 * (1 - _B + _B * self._doc_len[slots] / self._avgdl)))
            if len(self._dead_idx):
                scores[self._dead_idx] = 0.0
            return [(self._uuids[slot], float(scores[slot])) for slot in self._top(scores, k, has_negative)]

    def _top(self, scores: np.ndarray, k: int, has_negative: bool) -> np.ndarray:
        """Slots of the best k live documents: score descending, ties newest (highest slot) first."""
        alive = self._alive[: len(scores)]
        if has_negative:  # scores can be negative or zero: rank every live document
            live = np.flatnonzero(alive)
            return live[np.lexsort((-live, -scores[live]))][:k]
        positive = np.flatnonzero(scores > 0)
        if len(positive) > k:
            values = scores[positive]
            kth_largest = np.partition(values, len(values) - k)[len(values) - k]
            positive = positive[values >= kth_largest]  # keeps everything tied with the k-th score
        ranked = positive[np.lexsort((-positive, -scores[positive]))][:k]
        if len(ranked) < k:
            zeros = np.flatnonzero((scores == 0) & alive)[::-1][: k - len(ranked)]
            ranked = np.concatenate([ranked, zeros])
        return ranked
