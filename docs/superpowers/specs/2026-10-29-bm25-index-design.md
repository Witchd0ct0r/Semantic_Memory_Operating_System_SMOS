# BM25 inverted index and query threading — design

Status: approved by the Governor 2026-10-29 (Steps 1-2); Step 3 (benchmark error analytics) added the same day at their
request, to ship in the same release (0.3.4). Size: a project (one design, one plan, inline TDD).

## Why

Keyword (BM25) search is the largest cost of a query and grows with the size of the whole store, not with the
number of matching documents. `smos/memory/bm25_index.py` uses `rank_bm25.BM25Okapi`, whose `get_scores` runs a pure
Python loop over every document for every query word, then `query()` sorts all scores. It also holds one lock for the
whole scoring pass, so concurrent queries run one after another, and every add or remove rebuilds the entire index.

Measured on the target host (2 cores, synthetic corpus; the figures themselves are company data and are not in this
repository): the BM25 stage grew roughly linearly with segments, was about three quarters of a query at 25,000
segments, took query p95 past the 200 ms budget at about 20,000 segments, and throughput stayed flat at a few
requests per second whatever the client count, with the server using about one of its two cores (the Python
interpreter lock plus the index lock). Vector search and HNSW are not the problem (flat search is about 3% of a query).

## Target load (Governor, 2026-10-29)

- Trial: up to 10-15 clients asking at the same time. Production: 60 or more. Mostly reads.
- Ingestion is rare, off hours, from fewer than 10 registered parsers.
- One project is roughly 20,000-40,000 segments (8,000+ requirement items, 50-80 documents, code; the segment count
  is the assistant's estimate), growing as more projects are added, so 100,000+ must stay usable.
- Acceptable wait: 1-2 seconds for a person, more for an agent.

## Goals

1. BM25 query cost depends on the postings of the query's words, not on the store size.
2. Concurrent queries do not wait for each other at the BM25 step.
3. Adding and removing documents is incremental (no full rebuild per call or per ingest chunk).
4. Output is **identical** to today's: same scores, same order, same tie-breaks, same zero-score padding.
5. Use both cores better, checked by measurement (see Step 2).

## Non-goals

HNSW; multiple server processes (read replicas) - decided after measuring this work; any change to ranking, tokenizing,
fusion or the stored data; a persisted BM25 index (it is still built from SQLite at startup, as now).

## Step 1 - inverted index (`smos/memory/bm25_index.py`)

`BM25Index` keeps its public methods and behavior: `build`, `update`, `add_batch`, `remove`, `remove_batch`, `query`.
Callers (`vector_store.py`, `context_builder.py`, `exact_retrieval.py`, `requirement_context.py`, `bench/phases.py`) do
not change.

**Scoring must reproduce `BM25Okapi` exactly** (k1 = 1.5, b = 0.75, epsilon = 0.25):
- `idf(t) = ln(N - n_t + 0.5) - ln(n_t + 0.5)`, N = live documents, n_t = live documents containing t. Words whose idf
  is negative get `epsilon * average_idf`, where average_idf is the mean of the raw idf over all words in the vocabulary
  (negative ones included) - this global value changes on every add or remove, so it is recomputed (vectorised) after each
  mutation, not maintained incrementally.
- `score(d) = sum over query tokens q (a repeated query word counts each time, in order) of idf(q) * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avgdl))`,
  avgdl = mean live document length. Same operation order as `rank_bm25` so ties break the same way in floating point.
- Ranking: score descending, ties by insertion order, newest first. If fewer than k documents score above zero, the
  result is padded with zero-score documents, newest first (callers drop scores that are not above 0, but the padding is
  part of today's contract and is kept). A degenerate corpus where weights can be zero or negative (very small stores)
  takes a slower full-ranking path so the output stays identical.

**Structure (builder's call on details):**
- Documents occupy slots in insertion order (slot number = insertion order, never reused). Removal marks a slot dead and
  decrements the document frequency of its words using that document's stored word counts; dead slots are skipped at
  query time and the structure is compacted (order preserved) when the dead share passes a threshold.
- Postings per word: numpy arrays of slot numbers and term frequencies, appended in amortised O(1).
- Query: for each query word add its contribution to a score array with numpy, take the top k with `argpartition`
  (including everything tied with the k-th score), order those candidates by (score, slot).
- **Concurrency:** many queries at once, mutations exclusive. A small read-write lock (queries share it, add/remove take
  it exclusively) replaces the single mutex. Mutations finish their derived data (idf statistics, array views) before
  releasing, so a query never changes shared state. Ingest is rare and off hours, so a write briefly blocking reads is
  accepted.
- The thread-safety contract of `VectorStore` (writes under `VectorStore._lock`) is unchanged.

**Dependency:** `rank-bm25` stays in `pyproject.toml` (the tests use it as the reference oracle, and removing it would
change a file that forces a rebuilt image). The runtime no longer imports it.

## Step 2 - embedding threads (`smos/memory/embeddings.py`)

Torch probably starts two threads per embedding; with several queries at once on two cores this oversubscribes the CPU.
Add `SMOS_TORCH_THREADS` (positive integer; unset keeps today's behavior), applied with `torch.set_num_threads` when the
model is loaded. It is read from the environment, which compose already fills from `.env`, so the Governor changes it on
the server with an edit and `docker compose up -d`, without a rebuilt image. The benchmark's hardware block records the
value so runs can be compared. The default stays "unset" until a Standard run on the target host with 1 and with 2
shows which is better; then the default is changed in a later small release.

## Step 3 - benchmark error analytics (`smos/bench/httpload.py`, `runner.py`, report)

Added at the Governor's request (2026-10-29) so the 0.3.4 release can explain the intermittent writer errors of the
mixed read/write test, which today are only counted. Everything below is additive: result schema stays 1, older stored
results and the report still render without the new key.

- **Client side.** A small thread-safe collector records every failed request in the sweep steps, the mixed test (reads
  and the writer, alone and with a writer) and the latency probes (which today silently drop non-200 answers): the phase,
  the request kind (query, document ingest, node text), the category (`http <status>`, `timeout`, or the exception class),
  seconds since the step started, the latency, and a short message (the API's error text, or the exception message),
  truncated to 200 characters. Never request bodies, headers or tokens.
- **Server side.** The throwaway server's log is deleted with the run, so the cause of a 500 is lost. When any request
  failed, the runner reads the server log before cleanup and keeps, per distinct exception, the final line of each
  traceback (`ErrorType: message`, truncated, with a count and the first/last time), plus the number of lines mentioning
  a timeout or a locked database. No paths, no full tracebacks. (Builder's call on the exact patterns.)
- **Result.** `result["errors"] = {"total", "groups": [{phase, request, category, count, first_s, last_s, example}], "server": {"exceptions": [{error, count}], "locked_lines", "timeout_lines"}}`, groups capped (say 50) with a "more not shown" count. Per-step `errors` counts stay.
- **Report.** An "Errors" section appears on the report page only when `total > 0` (grouped table: where, what, how
  many, when, example, then the server-side lines); with zero errors the page says so in one line. The JSON export
  contains it unchanged. The comparison table is not affected.
- **Tests:** collector grouping/truncation/thread-safety; a fake server that returns 500 and drops connections to prove
  both mixed-test paths record detail; the view renders with and without the key; the log scan on a sample log.

## Acceptance

- **Equivalence (the main guard):** a differential test builds random corpora (varied sizes, repeated words, duplicate
  query words, empty content, tiny stores, interleaved adds and removes) and compares `query()` against a `BM25Okapi`
  reference: same uuids in the same order, scores equal within float tolerance. The existing BM25, hybrid-retrieval and
  exact-term tests pass unchanged. Mutation-check the new guard tests once (break the formula on purpose, see them fail).
- **Concurrency test:** two threads querying while a third adds and removes; no exceptions, results always consistent with
  either the state before or after each mutation.
- **Speed (measured, here and on the target host):** at 25,000 segments the BM25 stage is at most one fifth of its old
  time, and a Standard run gets query p95 under the 200 ms budget with a clear rise in requests per second (aim: 3x or
  better). Ingest rate at 25,000 segments no longer falls below its rate at 5,000.
- **Errors:** a deliberately failing request in the test harness shows up in the result with its status, category, time
  and the server-side exception line; a run with no errors produces none and the page says so.
- No new migration; no change under `Dockerfile`, `compose.yaml`, `pyproject.toml`, `models/` or `smos/launcher.py`, so
  the release is an ordinary dashboard update.

## Out of scope but noted

- The cause of the occasional writer errors in the mixed test stays unknown until a Debian run with Step 3 shows it;
  fixing it is a follow-up.
- A heavy latency tail around 10,000 segments seen in both Standard runs (stalls well above the mean); recheck after.
- Embedding (15-30 ms of CPU per query) becomes the largest stage afterwards; batching concurrent query embeddings or a
  second process are the next levers if the measured numbers still fall short of the 1-2 second target at 60 clients.

## Calls made by the assistant (for the Governor to check)

- Read-write lock rather than copy-on-write snapshots: simpler, and writes are rare.
- Keep `rank-bm25` as a dependency for the sake of the image fingerprint and as the test oracle.
- Keep the zero-score padding and the newest-first tie order for strict equivalence, even though callers filter zeros.
- `SMOS_TORCH_THREADS` ships unset-by-default; the default changes only after a measured comparison.
