# EU-law toolchain benchmark — SMOS tools vs. raw file reading, on LegalAdapter

**Status:** approved design, ready for implementation planning.
**Depends on:** Phase 5.5b (merged, `a57bf09`) — `LegalAdapter` structural grammar +
`extract_references()`, so schema-driven ingestion of legal-shaped text actually
populates the memory graph before this benchmark measures retrieval over it.
**Not part of the Phase 5 sub-phase ladder.** A standalone validation, requested by the
Governor 2026-08-05, that extends `docs/superpowers/specs/2026-07-27-toolchain-benchmark-design.md`'s
methodology (SMOS tools vs. raw file reading, real subagents, real token accounting) from
a markdown/Kubernetes-docs corpus to a real, non-synthetic legal corpus ingested via
`LegalAdapter`. Complements `tests/bench_legal_regulations.py`, which already proved the
adapter's cross-reference mechanism works on a small synthetic fixture — this run checks
whether that result, and SMOS's general token/quality value proposition, both hold on
real EU legislative text.

## 1. Goal

Two questions, answered by one run:

1. **Toolchain value on legal text.** For a real EU-law corpus, does an agent using
   SMOS's semantic-query/recall tools spend meaningfully fewer tokens than an agent
   reading the raw source files directly, at comparable answer quality? Direct
   law-domain analogue of the Kubernetes-docs result (67% fewer tokens, comparable
   quality, one flagged hallucination).
2. **`LegalAdapter` on real text.** `LegalAdapter`'s cross-reference graph-rescue has
   only been proven against a synthetic bench corpus (`tests/bench_legal_regulations.py`,
   entirely fictional citations by design) and a 2-provision pytest fixture
   (`tests/test_legal_adapter.py`). Does its structural grammar (`Article \d+`, etc.) and
   defined-term heuristic actually hold up on real, unmodified EU legislative text —
   and does that graph-rescue visibly change what either subagent could retrieve in this
   run, not just work in isolation?

This is a real end-to-end run (live subagents, live token accounting), not a synthetic
monkeypatch benchmark. It complements, rather than replaces, `tests/bench_legal_regulations.py`.

## 2. Scope for this task

**In scope:**
- A designated sandbox project directory inside this repo, isolated from this repo's own
  dogfood `.smos/` data — `sandbox/eu-law-benchmark/`, gitignored, same pattern as
  `sandbox/toolchain-benchmark/`.
- Pulling and curating a real EU-law corpus (GDPR excerpt + Regulation (EU) 2018/1807 in
  full) from EUR-Lex into that sandbox, under EUR-Lex's explicit CC BY 4.0 reuse terms.
- Ingesting that corpus into a genuinely separate `.smos/` store, via the schema-driven
  path with `LegalAdapter()` explicitly selected (not auto-detected, not `MarkdownAdapter`).
- A 10-question, cross-document-synthesis-only question set (GDPR ↔ 2018/1807), grounded
  in the actual pulled text.
- Two subagent runs answering the same 10 questions: one using SMOS tools, one using raw
  `Read`/`Glob` only.
- Token usage capture for both runs, from each run's own usage report.
- Orchestrator-judged (this session, with the Governor) scoring of both answer sets against
  short reference answers, plus an explicit verdict on whether `LegalAdapter`'s graph-rescue
  visibly helped any answer, and a written comparison.

**Explicitly out of scope:**
- Any change to SMOS or `LegalAdapter` production code. This is a measurement exercise
  against the system as it stands post-5.5b; if it surfaces a real product gap, that
  becomes a future phase (5.5c or otherwise), not a fix folded into this task.
- A third-party/independent LLM judge subagent — the Governor's call, as with the k8s
  benchmark, is that this session does the judging directly (see §7).
- Statistical rigor beyond 10 questions (no repeated trials, no confidence intervals) —
  a directional check, not a paper.
- Hungarian-domestic law (njt.hu) as a source. njt.hu's Terms of Use explicitly prohibit
  automated/bulk downloading and database construction from its text (verified
  2026-08-05); EUR-Lex's Decision 2011/833/EU + CC BY 4.0 notice explicitly permits reuse
  for commercial or non-commercial purposes with attribution. This corpus uses EU law
  specifically because it has a clean, unambiguous reuse right — not as a permanent
  substitution for a future Hungarian-domestic-law benchmark, which would need its own
  sourcing plan (e.g. hand-copied excerpts under njt.hu's personal-use/citation
  allowance) if ever pursued.

## 3. Why an in-session `Agent`-tool dispatch can't isolate the SMOS store

Identical constraint to the k8s benchmark (`docs/superpowers/specs/2026-07-27-toolchain-benchmark-design.md`
§3): SMOS is registered as a **user-scope** MCP server. Its `server.py` binds its data
directory once, at subprocess startup, to `Path.cwd()`:

```python
_PROJECT_ROOT = Path.cwd()
_DATA_DIR = _PROJECT_ROOT / ".smos"
```

That subprocess is already running for this session, bound to this repo's own root. A
subagent dispatched via the in-session `Agent` tool shares this session's existing MCP
connection — it cannot get a different, sandbox-scoped `.smos/` store just by being told
to operate in a different directory.

**Consequence:** the SMOS-tool-using half of this benchmark needs its own Claude Code
process, launched with cwd = `sandbox/eu-law-benchmark/`, so a fresh `smos-server`
subprocess binds to that directory instead.

## 4. Design

### 4.1 Corpus source and reuse terms

**Source:** EUR-Lex, Hungarian-language text (`https://eur-lex.europa.eu/legal-content/HU/TXT/HTML/...`).

- **GDPR (Regulation (EU) 2016/679), CELEX 32016R0679** — a hand-picked subset of
  ~25-30 articles: the chapters Regulation 2018/1807 actually cross-references
  (territorial/material scope, cross-border transfer provisions) plus enough
  surrounding structure for genuine synthesis questions. Exact article list finalized
  after the text is pulled and read in full — cannot write grounded cross-document
  questions blind against article numbers alone.
- **Regulation (EU) 2018/1807 on the free flow of non-personal data, CELEX 32018R1807**
  — in full (~11 articles). Chosen as the companion regulation because it genuinely
  cross-references specific GDPR provisions (it carves out its own scope relative to
  GDPR's personal-data rules, rather than a vague "see also"), and is obscure enough
  relative to GDPR that an LLM is unlikely to have it memorized at article-level
  granularity — see §7 for why this matters more here than it did for the k8s corpus.

**Reuse basis:** verified 2026-08-05. `eur-lex.europa.eu/robots.txt` disallows specific
functional endpoints (export/print/autocomplete/account features, `Crawl-delay: 10`) but
not legal-content document pages. The EUR-Lex legal notice states reuse of legislative
text is permitted for commercial or non-commercial purposes under Decision 2011/833/EU,
under CC BY 4.0 (source attribution required). This is a substantively different
authorization than njt.hu's ToS, which explicitly forbids bulk/automated download and
database construction (also verified 2026-08-05) — do not extend this design's crawl
approach to njt.hu without a fresh reuse-terms check.

### 4.2 Acquisition

A script under `sandbox/eu-law-benchmark/scripts/pull_corpus.py` (or `.sh` — decided at
implementation time, whichever fits this repo's existing script conventions better):

1. Fetches each selected article's HU-language EUR-Lex page individually, sleeping
   ≥10s between requests (`Crawl-delay: 10`). ~40 articles total → under 10 minutes.
2. Strips HTML to plain text, preserving `Article N` markers as literal boundary
   lines — so `LegalAdapter.structural_boundaries()` matches them directly with no
   markdown conversion step. Verify the stripped output actually contains
   recognizable `Article \d+` boundaries before trusting the rest of the pipeline;
   EUR-Lex's HTML structure should be re-checked against the live page at
   implementation time, not assumed from this spec.
3. Writes one file per regulation (or per-article — whichever makes question-writing
   easier, decided at implementation time) into `sandbox/eu-law-benchmark/corpus/`.
4. Writes `SOURCE.md`: CELEX numbers, retrieval date, EUR-Lex URLs, and the CC BY 4.0
   attribution line the reuse notice requires.

`sandbox/eu-law-benchmark/` is gitignored (add alongside the existing
`sandbox/toolchain-benchmark/` entry) — the pulled legal text itself isn't committed,
only the script and `SOURCE.md`.

### 4.3 Isolation mechanism

Identical mechanism to the k8s benchmark (§4.2 of that spec): both the ingestion step
and the SMOS-tool-using answer run happen via a headless `claude -p` subprocess, cwd =
`sandbox/eu-law-benchmark/`:

```
cd sandbox/eu-law-benchmark
claude -p "<prompt>" \
  --model haiku \
  --output-format json \
  --allowedTools "mcp__smos__tool_ingest_document mcp__smos__tool_semantic_query mcp__smos__tool_recall ..."
```

`--allowedTools` scoped narrowly to exactly the tools each step needs — not a blanket
`--dangerously-skip-permissions`; the exact allowed-tool list is confirmed as part of
the implementation plan.

The raw-`Read` baseline needs no special isolation — a normal in-session `Agent` tool
dispatch (`model: "haiku"`), pointed at `sandbox/eu-law-benchmark/corpus/`, instructed
to answer using only `Read`/`Glob`, no SMOS tools.

### 4.4 Ingestion

Via the same headless-`claude -p`-from-sandbox-cwd mechanism as §4.3. The call to
`do_ingest_document_schema()` explicitly passes `adapter=LegalAdapter()` — not
auto-detected, not `MarkdownAdapter`. This is the detail that makes the run a real test
of the adapter and not just of SMOS's generic retrieval; get it wrong (e.g. falling
back to markdown/no adapter) and the run silently stops testing what it's meant to
test. Exact tool signature verified against the current source at plan-writing time,
per this project's standing rule against trusting a spec snippet's signature without
re-reading the real file first.

### 4.5 Question set

10 questions, all requiring synthesis across GDPR **and** Regulation 2018/1807 — no
single-hop lookups, and no question fully answerable from GDPR alone (see §7).
Questions drafted after the corpus is pulled and read in full. Each question gets a
short reference answer, written by the orchestrator directly from the corpus text, used
for scoring in §4.6 — not shown to either subagent.

Example shape (illustrative, not final — real questions come from the real pulled
articles): "Regulation 2018/1807 requires Member States to justify data-localization
requirements for non-personal data. If a dataset is a *mix* of personal and
non-personal data, which regulation's territorial-scope rules govern the personal-data
portion, and does 2018/1807 change GDPR's transfer rules for that portion?" — requires
combining 2018/1807's mixed-dataset provision with GDPR's own scope articles.

### 4.6 Scoring

The orchestrator (this session), together with the Governor, reads both subagents'
answers to each question side by side against the reference answer and scores them —
no separate judge subagent dispatch. Scoring dimensions:
- Does the answer contain the facts the reference answer has, and is it free of
  fabrication not supported by the corpus?
- Does the answer's specificity (exact article numbers, exact carve-out language) look
  like it came from the retrieved text, or from generic pretrained recall? (§7)

A per-question verdict (comparable / A better / B better, with a one-line reason) is
sufficient; no numeric rubric is fixed here. Separately, an explicit verdict on whether
`LegalAdapter`'s graph-rescue visibly changed what either subagent could retrieve for
any question — not just whether the mechanism works in isolation (already shown by
`tests/bench_legal_regulations.py`), but whether it mattered *in this run*.

### 4.7 Token measurement

Identical to the k8s benchmark (§4.6 of that spec): read directly from each run's own
usage report (the `Agent` tool's usage metadata for the baseline; the `usage` field in
`claude -p --output-format json` for the SMOS-tool run). Fall back to reading the run's
session transcript under `~/.claude/projects` only if one of these two sources turns out
incomplete or unavailable.

### 4.8 Output

A short written comparison: token cost and quality verdict per question, for both runs;
an overall read on whether SMOS's tools won, lost, or were a wash on this corpus; and a
separate, explicit read on whether `LegalAdapter` visibly mattered in this run. Where
this write-up lives (a new file under `docs/`, or folded into the next
`docs/HANDOFF.md` update) is an implementation-time decision.

## 5. Error handling / edge cases

- If the headless `claude -p` subprocess fails outright (MCP server fails to start in
  the sandbox cwd, or a tool call errors), that's a run failure to report as-is, not
  something to silently retry into a different mechanism.
- If EUR-Lex's page structure, robots.txt, or reuse terms have changed since this spec
  was written (2026-08-05), re-verify before pulling; don't assume §4.1's paths/terms
  are still current.
- If the stripped HTML doesn't yield recognizable `Article N` boundaries for
  `LegalAdapter.structural_boundaries()` to match, that's a pipeline bug to fix in the
  acquisition script (§4.2), not a reason to fall back to a different adapter — the
  whole point of this run is testing `LegalAdapter` specifically (§1, goal 2).
- If 10 cross-document questions turn out too few to see a clear signal on either goal,
  that's a valid — if inconclusive — result to report, not a reason to silently expand
  scope mid-run.

## 6. Testing

This task produces a one-off measurement, not production code — no new pytest suite.
"Testing" here means:
- Verify the sandbox `.smos/` store actually differs from this repo's own `.smos/`
  store after ingestion (e.g. compare row counts or `ls` both directories) before
  trusting isolation held.
- Spot-check at least one ingested segment's boundaries to confirm `Article N` markers
  landed where expected (§4.2/§5) before trusting downstream retrieval results.
- Spot-check at least one SMOS-tool answer manually to confirm it's genuinely citing
  retrieved content rather than parametric knowledge (§7) — a materially bigger risk
  here than it was for the k8s corpus.

## 7. Known threat to validity — flag, don't fully solve here

GDPR is, if anything, *more* universally represented in any LLM's pretraining data than
Kubernetes docs were for the original benchmark. Both subagents — especially the
raw-`Read` baseline, and even the SMOS-tool run if it under-uses its tools — could
answer many GDPR-only questions correctly from parametric knowledge alone, without
reading anything, which would make the token comparison meaningless.

**Mitigation specific to this corpus** (stronger than the k8s spec's generic
"avoid generic how-does-X-work questions" guidance, because the risk is stronger):
- Every question must hinge on the *interaction* between a specific GDPR article and a
  specific Regulation 2018/1807 article — not "what does GDPR say about X" in
  isolation, which any model answers cold from memory. If a question is fully
  answerable from GDPR alone, it is disqualified and rewritten.
- Regulation 2018/1807 is deliberately the low-memorization anchor (§4.1) — prefer
  questions turning on its exact provisions, carve-outs, or cross-reference wording,
  details unlikely to be memorized precisely even where the general GDPR topic is
  famous.
- The specificity check in §4.6 is mandatory, not optional, for every question in this
  run's write-up — not a spot-check.

Not fully solved by this design — a judgment call made at question-writing and scoring
time, same as the k8s spec acknowledged for its own corpus, but the mitigation strategy
(anchor on the obscure companion document) is specific to this domain and stated
explicitly so it isn't lost at implementation time.
