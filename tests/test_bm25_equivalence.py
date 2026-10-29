"""The numpy BM25 index must return exactly what the old rank_bm25 implementation returned."""
from __future__ import annotations

import random
import subprocess
import sys

import pytest
from pathlib import Path

from smos.memory.bm25_index import BM25Index
from tests.bm25_reference import ReferenceBM25Index, assert_same_ranking, random_corpus, random_query


ROOT = Path(__file__).resolve().parent.parent


def _both(corpus: list[tuple[str, str]]) -> tuple[BM25Index, ReferenceBM25Index]:
    new, ref = BM25Index(), ReferenceBM25Index()
    new.build(corpus)
    ref.build(corpus)
    return new, ref


@pytest.mark.parametrize("seed", range(40))
def test_query_matches_reference_on_random_corpora(seed: int) -> None:
    rng = random.Random(seed)
    new, ref = _both(random_corpus(rng, rng.randint(1, 60)))
    for _ in range(25):
        text, k = random_query(rng), rng.choice([1, 5, 10, 100])
        assert_same_ranking(new.query(text, k), ref.query(text, k))


@pytest.mark.parametrize("text,k", [
    ("", 5), ("!!! ???", 5), ("w1 w1 w1", 5), ("neverseen unknownword", 5), ("w1 w2", 0), ("w1 w2", 1000),
])
def test_degenerate_queries_match_reference(text: str, k: int) -> None:
    new, ref = _both(random_corpus(random.Random(7), 20))
    assert_same_ranking(new.query(text, k), ref.query(text, k))


@pytest.mark.parametrize("n_docs", [1, 2, 3])
def test_tiny_corpora_with_zero_or_negative_idf_match_reference(n_docs: int) -> None:
    # "common" is in every document (negative idf), "half" is in exactly half of them (zero idf at even sizes)
    corpus = [(f"d{i}", "common " + ("half" if i % 2 == 0 else "other") + f" extra{i}") for i in range(n_docs)]
    new, ref = _both(corpus)
    for text in ("common", "half", "common half other", "extra0 common", "nothing"):
        assert_same_ranking(new.query(text, 10), ref.query(text, 10))


def test_new_index_does_not_use_rank_bm25_at_runtime() -> None:
    code = "import sys, smos.memory.bm25_index; print('dirty' if 'rank_bm25' in sys.modules else 'clean')"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
    assert done.stdout.strip() == "clean", done.stdout + done.stderr
