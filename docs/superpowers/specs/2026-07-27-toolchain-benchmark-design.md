# Toolchain end-to-end benchmark — SMOS tools vs. raw file reading

**Status:** approved design, ready for implementation planning.
**Depends on:** Phase 5.5a (merged, `ad9122f`) — reference extraction & domain tagging, so
markdown ingestion actually populates the memory graph and domain tags before this benchmark
measures retrieval over it.
**Not part of the Phase 5 sub-phase ladder.** A standalone validation of SMOS's core value
proposition (fewer tokens at comparable answer quality vs. raw file reading), requested by the
Governor 2026-07-27, run end-to-end through real subagents rather than the internal
monkeypatch-based retrieval-metric benchmarks (`tests/bench_*.py`) that already exist.

## 1. Goal

Answer one question: for a real long-form documentation corpus, does an agent that answers
questions using SMOS's semantic-query/recall tools spend meaningfully fewer tokens than an
agent that answers the same questions by reading the raw source files directly — at comparable
answer quality?

This is a real end-to-end run (live subagents, live token accounting), not a synthetic
monkeypatch benchmark. It complements, rather than replaces, `tests/bench_*.py`.

## 2. Scope for this task

**In scope:**
- A designated sandbox project directory inside this repo, isolated from this repo's own
  dogfood `.smos/` data (already created: `sandbox/toolchain-benchmark/`, gitignored — see
  `.gitignore` and `docs/HANDOFF.md`).
- Pulling and curating a real long-form corpus (Kubernetes docs subset) into that sandbox.
- Ingesting that corpus into a genuinely separate `.smos/` store bound to the sandbox
  directory (not this repo's).
- A 10-question, cross-document-synthesis-only question set grounded in the actual corpus text.
- Two subagent runs answering the same 10 questions: one using SMOS tools, one using raw
  `Read`/`Glob` only.
- Token usage capture for both runs, from each run's own usage report.
- Orchestrator-judged (this session, with the Governor) scoring of both answer sets against
  short reference answers, and a written comparison.

**Explicitly out of scope:**
- Any change to SMOS production code. This is a measurement exercise against the system as it
  stands post-5.5a; if it surfaces a real product gap, that becomes a future phase, not a fix
  folded into this task.
- A third-party/independent LLM judge subagent — the Governor's call is that this session does
  the judging directly (see §6).
- Statistical rigor beyond 10 questions (no repeated trials, no confidence intervals) — this is
  a directional check, not a paper.

## 3. Why an in-session `Agent`-tool dispatch can't isolate the SMOS store

SMOS is registered as a **user-scope** MCP server (`smos mcp add ... --scope user`). Its
`server.py` binds its data directory once, at subprocess startup, to `Path.cwd()`
(`smos/server.py:62-65`):

```python
# Each project gets its own .smos/ directory. The MCP server is started
# as a subprocess by Claude Code from the project root, so cwd == project root.
_PROJECT_ROOT = Path.cwd()
_DATA_DIR = _PROJECT_ROOT / ".smos"
```

That subprocess is already running for this session, bound to this repo's own root
(`C:\private\semantic_memory`). A subagent dispatched via the in-session `Agent` tool shares
this session's existing MCP connection — it cannot get a different, sandbox-scoped `.smos/`
store just by being told to operate in a different directory. There is no per-call or
per-subagent cwd override; the binding happens once, at server-process launch.

**Consequence:** the SMOS-tool-using half of this benchmark cannot run as a plain in-session
`Agent` dispatch. It needs its own Claude Code process, launched with cwd =
`sandbox/toolchain-benchmark/`, so a fresh `smos-server` subprocess binds to that directory
instead.

## 4. Design

### 4.1 Corpus

Shallow-clone `kubernetes/website` (CC BY 4.0) into a scratch temp location (not committed
anywhere). Hand-pick ~15-25 markdown pages from `content/en/docs/concepts/` and
`content/en/docs/tasks/`, spanning a few related topic areas (e.g. workloads, networking,
storage) so there's real cross-page relationship to ask synthesis questions about. Copy the
selected pages into `sandbox/toolchain-benchmark/corpus/`. Add a one-line `SOURCE.md` noting
the upstream repo, commit hash, and license — the directory is gitignored, but the note costs
nothing and avoids future confusion about provenance.

### 4.2 Isolation mechanism

Both the ingestion step and the SMOS-tool-using answer run happen via a **headless `claude -p`
subprocess**, launched via the Bash tool with its working directory set to
`sandbox/toolchain-benchmark/`:

```
cd sandbox/toolchain-benchmark
claude -p "<prompt>" \
  --model haiku \
  --output-format json \
  --allowedTools "mcp__smos__tool_ingest_document mcp__smos__tool_semantic_query mcp__smos__tool_recall ..."
```

Because SMOS is a user-scope MCP server bound to cwd at startup, launching `claude` from this
directory spins up its own `smos-server` subprocess with its own `.smos/` store under
`sandbox/toolchain-benchmark/.smos/` — genuinely isolated from this repo's own store, no shared
state, no dogfood contamination.

Permissions are scoped narrowly via `--allowedTools` to exactly the SMOS MCP tools the task
needs (ingestion tool(s) for the ingest run; query/recall tools for the answer run) plus `Read`
if the prompt needs to enumerate corpus files by name. **Not** a blanket
`--dangerously-skip-permissions` — per this repo's working agreements on autonomy, the exact
allowed-tool list gets confirmed as part of the implementation plan, not assumed here.

The raw-`Read` baseline subagent has no MCP/cwd dependency and needs no special isolation — it
runs as a normal in-session `Agent` tool dispatch (`model: "haiku"`), pointed at
`sandbox/toolchain-benchmark/corpus/` and instructed to answer using only `Read`/`Glob`, no
SMOS tools.

### 4.3 Ingestion

Via the same headless-`claude -p`-from-sandbox-cwd mechanism as §4.2, using
`tool_ingest_document` (or `tool_recursive_semantic_ingest`, if batch-ingesting the whole
`corpus/` directory in one call turns out cleaner than one call per file) against the copied
markdown pages. The exact tool choice is an implementation-time decision, made by reading the
current signatures of both tools before writing the plan — not assumed from this spec.

### 4.4 Question set

10 questions, all requiring synthesis across 2+ of the ingested pages — no single-hop
lookups. Questions get drafted after the corpus is actually pulled and read in full (can't
write grounded cross-document questions blind against page titles alone). Each question gets a
short reference answer, written by the orchestrator directly from the corpus text, used for
scoring in §4.5 — not shown to either subagent.

Example shape (illustrative, not final — real questions come from the real pages once chosen):
"How do a Deployment's rolling-update strategy and a PodDisruptionBudget interact during a
voluntary node drain?" — requires combining facts from the Deployments page and the
Disruptions page.

### 4.5 Scoring

The orchestrator (this session), together with the Governor, reads both subagents' answers to
each question side by side against the reference answer and scores them — no separate judge
subagent dispatch. Scoring dimension: does the answer contain the facts the reference answer
has, and is it free of fabrication not supported by the corpus. A simple per-question verdict
(comparable / A better / B better, with a one-line reason) is sufficient; no numeric rubric is
specified by this design — pick one at write-up time if a finer grain turns out useful.

### 4.6 Token measurement

Read directly from each run's own usage report:
- Baseline (`Agent` tool dispatch): the usage metadata the tool call itself returns.
- SMOS-tool run (headless `claude -p`): the `usage` field in the `--output-format json` result.

No separate token-counting pass over transcripts unless one of these two sources turns out
incomplete or unavailable, in which case fall back to reading the run's session transcript
under `~/.claude/projects` directly.

### 4.7 Output

A short written comparison: token cost and quality verdict per question, for both runs, plus
an overall read on whether SMOS's tools won, lost, or were a wash on this corpus. Where this
write-up lives (a new file under `docs/`, or folded into the next `docs/HANDOFF.md` update) is
an implementation-time decision, not fixed here.

## 5. Error handling / edge cases

- If the headless `claude -p` subprocess fails outright (e.g. MCP server fails to start in the
  sandbox cwd, or a tool call errors), that's a run failure to report as-is, not something to
  silently retry into a different mechanism — the isolation mechanism itself is part of what's
  being validated.
- If `kubernetes/website` licensing terms or repo structure have changed since this spec was
  written, re-verify before cloning; don't assume the paths in §4.1 are still current.
- If 10 cross-document questions turn out to be too few to see a clear signal (e.g. both runs
  score identically on every question), that's a valid — if inconclusive — result to report,
  not a reason to silently expand scope mid-run.

## 6. Testing

This task produces a one-off measurement, not production code — there is no new pytest suite.
"Testing" here means: verify the sandbox `.smos/` store actually differs from this repo's own
`.smos/`/`data/` stores after ingestion (e.g. compare row counts or `ls` both directories)
before trusting that isolation held, and spot-check at least one SMOS-tool answer manually to
confirm it's genuinely citing retrieved content rather than the model's own pretrained
Kubernetes knowledge (a real risk given how well-known Kubernetes docs are to any LLM — see §7).

## 7. Known threat to validity — flag, don't solve here

Kubernetes is extremely well-represented in any LLM's pretraining data. Both subagents —
especially the raw-`Read` baseline, and even the SMOS-tool run if it under-uses its tools —
could answer many of these questions correctly from parametric knowledge alone, without
actually reading anything, which would make the token comparison meaningless (a model that
reads nothing "wins" on tokens trivially). Mitigate by writing questions that hinge on
specifics likely to have drifted or be underspecified in pretraining (exact flag names,
version-specific behavior, or invented-but-plausible cross-references if needed) rather than
generic "how does X work" questions a model would answer the same way with zero tool calls.
Worth an explicit check during scoring (§4.5): does the answer's phrasing/specificity look like
it came from the retrieved text, or generic recall? Not solved by this design — a judgment call
for the write-up.
