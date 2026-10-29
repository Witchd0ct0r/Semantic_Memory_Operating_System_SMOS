"""Standalone: BM25 query and single-add cost, old rank_bm25 implementation against the numpy inverted index.

Run: python tests/bench_bm25.py   (not collected by pytest)
"""
from __future__ import annotations

import random
import statistics
import time

from smos.memory.bm25_index import BM25Index
from tests.bm25_reference import ReferenceBM25Index

SIZES = (5_000, 25_000, 100_000)
REFERENCE_MAX = 25_000  # the old implementation takes minutes per size beyond this
QUERIES = 100
VOCAB = [f"term{i}" for i in range(8_000)]
WEIGHTS = [1.0 / (i + 1) ** 0.8 for i in range(len(VOCAB))]


def corpus(rng: random.Random, n: int) -> list[tuple[str, str]]:
    return [(f"d{i}", " ".join(rng.choices(VOCAB, weights=WEIGHTS, k=40))) for i in range(n)]


def mean_query_ms(index, queries: list[str]) -> float:
    times = []
    for text in queries:
        started = time.perf_counter()
        index.query(text, 40)
        times.append((time.perf_counter() - started) * 1000)
    return statistics.fmean(times)


def main() -> None:
    rng = random.Random(1)
    queries = [" ".join(rng.choices(VOCAB, weights=WEIGHTS, k=6)) for _ in range(QUERIES)]
    print(f"{'segments':>9} {'old ms/query':>13} {'new ms/query':>13} {'ratio':>7} {'new build s':>12} {'200 adds s':>11}")
    for size in SIZES:
        docs = corpus(rng, size)
        new = BM25Index()
        started = time.perf_counter()
        new.build(docs)
        build_s = time.perf_counter() - started
        new_ms = mean_query_ms(new, queries)
        old_ms = None
        if size <= REFERENCE_MAX:
            old = ReferenceBM25Index()
            old.build(docs)
            old_ms = mean_query_ms(old, queries)
        started = time.perf_counter()
        for i in range(200):
            new.update(f"n{i}", " ".join(rng.choices(VOCAB, weights=WEIGHTS, k=40)))
        adds_s = time.perf_counter() - started
        ratio = f"{old_ms / new_ms:6.1f}x" if old_ms else "      -"
        print(f"{size:>9} {old_ms if old_ms else float('nan'):>13.2f} {new_ms:>13.2f} {ratio} {build_s:>12.2f} {adds_s:>11.2f}")


if __name__ == "__main__":
    main()
