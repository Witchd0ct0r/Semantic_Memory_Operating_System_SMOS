# Review package 8: BM25 inverted index, embedding threads, benchmark error analytics (release 0.3.4)

Eighth review round. Earlier: package 7 (the Benchmark page, resolved in `docs/benchmark-verification.md` section 7). This package covers
`git diff 20b5a59..HEAD` (20b5a59 is the 0.3.3 release commit; design `docs/superpowers/specs/2026-10-29-bm25-index-design.md`, plan
`docs/superpowers/plans/2026-10-29-bm25-index.md`). **This one changes the hot path of every query**: the keyword (BM25) half of hybrid
retrieval is rewritten from `rank_bm25` to a hand-written numpy inverted index with a new read-write lock, so a wrong score, a wrong
order or a deadlock affects every answer the service gives. Report only; do not edit.

## 1. Scope

- **BM25 (the main risk).** `smos/memory/bm25_index.py` (rewritten: postings per word, slots in insertion order, dead slots and
  compaction, global statistics refreshed after each mutation), `smos/memory/rwlock.py` (new). Callers are unchanged:
  `smos/memory/vector_store.py` (`build`, `update`, `remove`, `add_batch`, `remove_batch` under `VectorStore._lock`),
  `smos/compression/context_builder.py`, `smos/memory/exact_retrieval.py`, `smos/memory/requirement_context.py`, `smos/bench/phases.py`
  (`query`). Oracle: `tests/bm25_reference.py` is the **old class copied verbatim** (`ReferenceBM25Index`); equivalence tests in
  `tests/test_bm25_equivalence.py`; lock tests in `tests/test_rwlock.py`; micro-benchmark `tests/bench_bm25.py` (standalone).
- **Embedding threads.** `smos/memory/embeddings.py`: `SMOS_TORCH_THREADS` (`configured_threads`, `apply_threads`, validated in
  `warm_up`); `smos/bench/runner.py::hardware_info` records it; `.env.example`. Tests `tests/test_embedding_threads.py`.
- **Benchmark error analytics.** New `smos/bench/errorlog.py`; `smos/bench/httpload.py` (records failed requests, `BenchServer.log_path`);
  `smos/bench/runner.py` (passes the log through, digests the throwaway server's log, `result["errors"]`, a warning);
  `smos/admin/benchmark_view.py::_errors_section` and `smos/admin/templates/benchmark_report.html` (a "Request errors" section).
  Tests: `tests/test_bench_errorlog.py`, `tests/test_bench_httpload.py`, `tests/test_bench_runner.py`, `tests/test_bench_view.py`,
  `tests/test_bench_pages.py`.
- **Calibration.** `smos/bench/analysis.py`: comparison flags are now per metric (`NOISY_FLAGS` 25/40 for latency and throughput,
  `STEADY_FLAGS` 10/20 for ingest and startup), chosen from two Standard runs of identical code on the target host. The figures are
  company data and are deliberately not in the repository; judge the method, not the numbers.
- Docs touched: `ARCHITECTURE.md` (debt item 7 resolved), `docs/INSTALL.md`, `docs/runbooks/benchmark.md`, `docs/benchmark-verification.md`,
  `docs/HANDOFF.md`, `smos/__init__.py` (0.3.4).
- Not changed (on purpose, so the update works from the dashboard without a rebuilt image): `pyproject.toml` (`rank-bm25` stays listed
  because the tests use it and removing it would force a rebuilt image), `Dockerfile`, `compose.yaml`, `models/`, `smos/launcher.py`.

## 2. Run it

```bash
rtk proxy python -m pytest tests -q                       # expect 1071 passed (about 3.5 minutes)
rtk proxy python -m pytest tests/test_bm25_equivalence.py tests/test_rwlock.py -q   # the BM25 guard on its own, ~15 s
python -m tests.bench_bm25                                # old against new, per query and per single add (dev PC, ~1 minute)
```

The equivalence tests compare the new index with the old class on random corpora (40 seeds), random operation sequences (40 seeds,
adds, batches with repeated uuids, removes, unknown uuids, re-adds, queries after every step), a churn run that forces compaction,
tiny corpora with zero or negative idf, and degenerate queries. They tolerate float noise (`rel=1e-9`) and let documents whose reference
scores tie to 1e-12 swap inside the tie. The suite breaks the guard on purpose once (constant `b` changed, 40 of 50 tests failed).

## 3. What to attack, hardest first

1. **Is the new index really the old index?** Read `query`, `_word_idf`, `_refresh` and `_top` against the old class in
   `tests/bm25_reference.py`.
   - `rank_bm25` computes `average_idf` as a sequential Python sum over the vocabulary; `_refresh` uses a numpy sum with `np.log`, and
     per-word idf uses `math.log` (so non-negative weights are bit-identical, only the floor for negative-idf words can differ by a
     few ulp). Is any ranking able to change because of that, other than inside the tolerance the tests allow?
   - Tie order (score descending, then newest insertion first), zero-score padding newest first, repeated query words counting
     repeatedly, `k = 0`, `k` larger than the store, empty and punctuation-only queries, words in every document (idf below zero,
     replaced by `epsilon * average_idf`), `idf == 0` words skipped.
   - The ranking path when any weight is negative (`has_negative`) ranks every live document with `lexsort`. Is the output identical to
     the old sort, including for documents the query does not touch?
   - Postings store `float32` frequencies and `int32` slots; scoring converts to `float64`. Any count where that is lossy (frequencies
     above 2^24, more than 2^31 slots), and the `np.resize` growth of `_doc_len` and `_alive` (it fills new space with repeated old
     data; only `[:n_slots]` should ever be read).
2. **Mutation bookkeeping.** `_append`, `_kill`, `_finish`, `_load`. Document frequency and `_total_len` after removals; a word whose
   last document is removed (its posting is deleted; a later re-add recreates it); a uuid removed and added again; compaction
   (`_COMPACT_DEAD_SHARE = 0.25`) rebuilding from live documents in slot order; `_dead_idx` refreshed only on the non-compacting
   branch and zeroed into the scores at query time; an index whose last live document is removed (`_clear`); `build` called with
   duplicate uuids (the old class kept both rows, `_slot_of` keeps the last).
3. **Locking.** `RWLock` (writer preference, `Condition`): lost wake-ups, a writer that raises while waiting, a reader that raises inside
   `with`, a thread that holds `read()` and calls `write()` (deadlock; does any caller do that?), `VectorStore._lock` held while a
   BM25 mutation waits for readers (lock-order cycles with `_lock`, the new ingest locks, or the benchmark), starvation of readers by a
   stream of writers (ingest `add_batch` per chunk), and whether `query` truly changes no shared state (it should not).
4. **Performance cliffs the benchmark would not show.**
   - A query word present in nearly every document costs O(its postings) per query; is there a realistic corpus (requirements share
     boilerplate) where most query words are that common?
   - When most of the vocabulary appears in more than half of the documents, the average idf is negative, `_floor` is negative, and every
     query takes the O(N log N) `lexsort` path. How likely is that on templated requirement text, and is the fallback acceptable?
   - `_refresh` is O(vocabulary) after every mutation, including a single `store()`; compaction and `_load` are O(total words).
   - Memory: a `Counter` per document is retained (as the old class retained token lists) plus postings; estimate for 100,000+ segments.
5. **`SMOS_TORCH_THREADS`.** `apply_threads` runs before every encode because `torch.set_num_threads` is per calling thread on OpenMP
   builds and requests run in worker threads. Does calling it from many threads, often, have a cost or a side effect (thread pool
   re-creation, interaction with the benchmark's second model copy)? Is it right to validate in `warm_up` and still raise on a bad value
   at encode time? Unset means exactly the old behaviour: check that nothing else changed on that path.
6. **Error analytics.** `smos/bench/errorlog.py`: `message_from_body` on hostile or huge bodies, the truncation limit (200), what the
   stored text could leak (API error text for generated documents only, but check headers and tokens never enter), thread safety of
   `record`/`summary`/`add_server_log`, `add_server_log` on a very large or binary log (it streams line by line), the traceback state
   machine (chained exceptions count only the root; `ExceptionGroup`, nested tracebacks, a log cut off mid-trace), counts of "timeout"
   lines inflated by ordinary words, the 20-exception and 50-group caps. In `httpload.py`: the `since` base for "seconds since the step
   started", recording on a high-rate failing loop (timeouts), and that returned numbers and control flow of the existing probes are
   unchanged when `errors` is `None`.
7. **The report.** `_errors_section` and the template with hostile stored `errors` values (huge lists, NaN or infinity, wrong types,
   deeply nested data, HTML in messages); every value should reach the template as plain text that Jinja escapes.
8. **A defect the new error analytics exposed (pre-existing, not fixed in this release).** Running the benchmark smoke preset repeatedly
   (`runner.run` with `SMOKE`, about 3 runs in 10) produced one failed request in the "mixed: reads with writer" step: `POST /api/v1/query`
   answers 500 and the client sees a dropped connection a millisecond later. The captured server log (the digest now reads it) shows
   `SystemError: error return without exception set` raised at `smos/memory/vector_store.py::bump_recall`, `self._db.commit()`, called from
   `semantic_tools.semantic_query` while a document ingest runs in another request. The same symptom appeared in the first Quick run on the
   target host under release 0.3.3 (read and writer errors in the mixed test), so it is older than this work. What is known: `self._db` is
   one `sqlite3` connection (`check_same_thread=False`) shared by `VectorStore` (guarded by `VectorStore._lock`) and `GraphStore` (guarded by
   its own `_lock`; `context_builder` calls `store.graph.*` without holding `VectorStore._lock`), and several read paths may hold unfinished
   statements. A quick two-lock stress (three threads on `graph.get_node/get_edges`, one on `set_summary`, 15 s) did **not** reproduce it,
   so the cause is not proven. Please say what you think the mechanism is (an unfinished cursor when `COMMIT` runs, two locks over one
   connection, anything in `ingest` chunking) and the smallest safe fix; a fix is not part of this release.
9. **Calibration method.** Is it defensible to set 25/40 and 10/20 flags from one pair of identical runs, and to mark them provisional?
   Is `query_p95_ms` (a tail statistic from 100 samples) a poor comparison metric compared with the mean or p50?

## 4. Known limits and honest gaps

- The effect on a loaded 2-core server is **not measured yet**: the dev-PC micro-benchmark shows the BM25 stage roughly 80x faster at
  5,000 segments and 230x at 25,000, and 100,000 segments answering in about 1 ms, but whether both cores are then used (the GIL and
  torch threads) will be measured on the target host after release. That `SMOS_TORCH_THREADS` helps is a hypothesis; it ships unset.
- The old implementation crashed with a division by zero once only empty documents remained (`ZeroDivisionError` in `BM25Okapi`);
  the new one returns zero-score padding. The equivalence tests skip comparison when the old class crashes.
- Scores are not bit-identical to the old class in one narrow place (the negative-idf floor, see 3.1); the tests accept float noise and
  swaps inside exact-tie groups only.
- Only the dev PC (Windows) ran the new code; Linux behaviour of threads and the lock is the same Python code but unmeasured.
- The cause of the intermittent writer errors of the mixed read/write test is unknown; this release adds the means to see it, it does
  not fix it. The slow growth of ingest time (full-index rebuild per chunk) should disappear with the incremental index but is
  unmeasured on the target host.
- Deferred minors from earlier rounds are untouched (a page can hang up to 30 s while a benchmark start waits for the API drain; a
  stored NaN would make the JSON download fail).

## 5. How to report

Per finding: severity (Critical / Important / Minor), the file and line, the scenario (inputs or sequence of events), what goes
wrong, and a one- or two-sentence fix. Write the report to `docs/superpowers/review/2026-10-29-8-review.md`.
