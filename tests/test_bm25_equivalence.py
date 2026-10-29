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


def _random_operations(seed: int) -> None:
    rng = random.Random(seed)
    new, ref = BM25Index(), ReferenceBM25Index()
    first = random_corpus(rng, rng.randint(0, 20))
    new.build(first)
    ref.build(first)
    live = [uuid for uuid, _ in first]
    removed: list[str] = []
    counter = 1000
    words = [f"w{i}" for i in range(30)]

    def both(method: str, *args) -> bool:
        """Apply to both; False when the old class crashed (it divided by zero once only empty documents were left)."""
        getattr(new, method)(*args)
        try:
            getattr(ref, method)(*args)
        except ZeroDivisionError:
            return False
        return True

    for _ in range(25):
        op = rng.choice(["update", "update_existing", "add_batch", "remove", "remove_unknown", "remove_batch", "readd"])
        ok = True
        if op == "update":
            counter += 1
            uuid = f"d{counter}"
            ok = both("update", uuid, new_text(rng, words))
            live.append(uuid)
        elif op == "update_existing" and live:
            ok = both("update", rng.choice(live), new_text(rng, words))
        elif op == "add_batch":
            batch = []
            for _i in range(rng.randint(1, 6)):
                counter += 1
                batch.append((f"d{counter}", new_text(rng, words)))
            if rng.random() < 0.5:
                batch.append(batch[0])  # the same uuid twice inside one batch
            if live and rng.random() < 0.5:
                batch.append((rng.choice(live), "already present"))
            ok = both("add_batch", batch)
            live.extend(u for u, _ in batch if u not in live)
        elif op == "remove" and live:
            uuid = live.pop(rng.randrange(len(live)))
            ok = both("remove", uuid)
            removed.append(uuid)
        elif op == "remove_unknown":
            ok = both("remove", "nope")
        elif op == "remove_batch" and live:
            gone = rng.sample(live, min(len(live), rng.randint(1, 5))) + ["nope"]
            ok = both("remove_batch", gone)
            live = [u for u in live if u not in gone]
            removed.extend(u for u in gone if u != "nope")
        elif op == "readd" and removed:
            uuid = removed.pop()
            ok = both("update", uuid, new_text(rng, words))
            live.append(uuid)
        if not ok:
            return
        for _q in range(5):
            query, k = random_query(rng), rng.choice([1, 5, 10, 100])
            assert_same_ranking(new.query(query, k), ref.query(query, k))


def new_text(rng: random.Random, words: list[str]) -> str:
    return " ".join(rng.choices(words, weights=[1 / (i + 1) for i in range(30)], k=rng.randint(0, 20)))


@pytest.mark.parametrize("seed", range(40))
def test_random_operation_sequences_match_reference(seed: int) -> None:
    _random_operations(seed)


def test_heavy_churn_with_compaction_matches_reference() -> None:
    rng = random.Random(99)
    corpus = random_corpus(rng, 300)
    new, ref = _both(corpus)
    uuids = [u for u, _ in corpus]
    rng.shuffle(uuids)
    for start in range(0, 220, 20):
        batch = uuids[start:start + 20]
        new.remove_batch(batch)
        ref.remove_batch(batch)
    extra = [(f"x{i}", new_text(rng, [f"w{j}" for j in range(30)])) for i in range(80)]
    new.add_batch(extra)
    ref.add_batch(extra)
    for _ in range(30):
        query, k = random_query(rng), rng.choice([1, 5, 10, 100])
        assert_same_ranking(new.query(query, k), ref.query(query, k))
    assert new._compactions >= 1
    assert new._dead <= 0.25 * new._n_slots


def test_single_adds_do_not_rebuild_the_index() -> None:
    import time

    rng = random.Random(5)
    vocab = [f"term{i}" for i in range(5000)]
    corpus = [(f"d{i}", " ".join(rng.choices(vocab, k=8))) for i in range(20000)]
    index = BM25Index()
    started = time.perf_counter()
    index.build(corpus)
    build_s = time.perf_counter() - started
    started = time.perf_counter()
    for i in range(200):
        index.update(f"n{i}", " ".join(rng.choices(vocab, k=8)))
    assert time.perf_counter() - started < build_s, "200 single adds must cost less than one full build"


def test_only_empty_documents_do_not_crash_and_pad_with_zero_scores() -> None:
    index = BM25Index()
    index.build([("a", ""), ("b", "")])
    assert index.query("anything", 5) == [("b", 0.0), ("a", 0.0)]
