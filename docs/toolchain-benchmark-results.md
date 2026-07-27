# Toolchain benchmark results — SMOS tools vs. raw file reading

**Date:** 2026-07-27. **Plan:** `docs/superpowers/plans/2026-07-27-toolchain-benchmark.md`.
**Spec:** `docs/superpowers/specs/2026-07-27-toolchain-benchmark-design.md`.

## TL;DR

On this corpus and question set, the SMOS-tool run used **67% fewer tokens** than the
raw-file-reading baseline (41,869 vs. 128,324) at **comparable-to-slightly-better answer
quality** on 8 of 10 questions — but with one serious synthesis hallucination on Q1 that a
naive token-only read would miss. Two significant caveats affect how much weight this result
should carry: no `smos` MCP server is installed on this machine, so the SMOS run used direct
Python calls to the same retrieval functions instead of the real MCP tool surface; and the
measurement method for that run (cold Python process per query) makes its wall-clock time
look far worse than a real always-on MCP server would.

## Setup actually used (deviations from the plan)

- **Corpus:** 20 Kubernetes docs pages (`kubernetes/website` @ `b424574dfa5ed98d3bf4e0ff4510a7499a604c61`),
  ingested via `do_ingest_document_schema` into an isolated store at
  `sandbox/toolchain-benchmark/.smos/` — as planned (Task 3).
  **Baseline run:** in-session `Agent` tool dispatch (haiku), `Read`/`Glob` only, over the raw
  corpus files — as planned (Task 4).
- **SMOS-tool run — changed from the plan:** no `smos` MCP server is registered anywhere on
  this machine (`claude mcp list` confirmed empty; a headless `claude -p` attempt returned
  `permission_denials` on a fallback `Bash`/`rtk` call and a self-reported "tools not
  available" answer — not a real run). Rather than install SMOS globally to unblock this
  (a user-scope change affecting every Claude Code project on the machine), the Governor
  redirected the test to the underlying callable methods instead of the MCP transport:
  `sandbox/toolchain-benchmark/scripts/smos_tools.py` binds `semantic_query()` /
  `recall()` / `reconstruct()` — the exact functions `tool_semantic_query` / `tool_recall` /
  `tool_reconstruct` thinly wrap (`smos/server.py`) — to the isolated store, and the SMOS-tool
  subagent (haiku, `Bash` access, no `Read`) called them that way. This tests the real
  retrieval mechanism (vector+BM25+graph hybrid, the actual point of the benchmark) without
  testing MCP-protocol overhead specifically — a reasonable substitution for this question, but
  not what the plan originally specified.

## Token usage

| Run | Tokens (subagent) | Tool calls | Wall-clock |
|---|---|---|---|
| Baseline (raw Read) | 128,324 | 13 | 58.5s |
| SMOS tools (direct Python) | **41,869** | 22 | 676.4s |

SMOS used **32.6%** of the baseline's tokens (a 3.07x reduction). Tool-call count is higher for
SMOS (22 vs. 13) — many small targeted queries vs. fewer, larger raw file reads — consistent
with the core mechanism being tested (retrieval over ingest-time-compressed segments vs. reading
whole documents). **Wall-clock time is not a fair comparison here and shouldn't be read as a
cost of SMOS's retrieval mechanism**: each of the 22 `smos_tools.py` invocations is a cold
Python process that reloads the embedding model, FAISS index, and BM25 index from disk before
answering a single query — a real MCP server keeps all of that resident in memory across calls.
The ~11-minute wall-clock time is an artifact of the MCP-unavailable workaround, not a property
of SMOS itself.

## Per-question scoring

Orchestrator-judged against hand-written reference answers (not shown to either subagent),
per the design spec's scoring method. Full question text and reference answers:
`sandbox/toolchain-benchmark/questions.md` (gitignored — see that file locally, not tracked in
git since it lives under `sandbox/`).

| # | Topic | Verdict | Why |
|---|---|---|---|
| Q1 | Rolling update vs. PDB | **Baseline better** | Baseline matched the reference exactly, quoting the source directly. **SMOS's retrieved text was correct** (its own tool output stated PDBs don't limit rolling updates) **but its final answer asserted the opposite** ("the PDB can block or slow the rollout... can pause the rolling update") — a synthesis hallucination contradicting its own retrieved evidence. The most serious finding of this run. |
| Q2 | StatefulSet identity vs. scale-down blocking | **SMOS better** | SMOS's answer (ordinal identity, ordered termination, contrast with ReplicaSet fungibility) matches the reference mechanism precisely. Baseline drew from the task page's troubleshooting section ("can't tell if unhealthy is transient or permanent") instead of the concept-page identity mechanism the question specifically asked for. |
| Q3 | HPA scale-up/down asymmetry | **SMOS better** | SMOS correctly states no stabilization window for scale-up (immediate) and cites a stabilization window range for scale-down. Baseline is vaguer ("within a minute or so" for scale-up, which understates "immediate") and doesn't name the stabilization-window mechanism. |
| Q4 | ClusterIP vs. headless DNS | Comparable | Both correct, both match the reference. |
| Q5 | Ingress requires a Service | **Baseline better** | Baseline directly confirms the mainstream case (`service.name`+`service.port`, matching the reference). SMOS's answer focused on the `Resource` backend type (a rarer, non-Service Ingress backend), missing the main point the question and reference were actually about. |
| Q6 | NetworkPolicy + DNS | Comparable | Both correct on the core DNS-blocking claim; baseline slightly more complete (also restates `podSelector`'s Service-independence). |
| Q7 | PV reclaim vs. emptyDir | Slight edge SMOS | SMOS explicitly names the Retain-vs-Delete reclaim-policy mechanism; baseline's "lifecycle independent of the Pod" framing is correct but less specific than the reference. |
| Q8 | revisionHistoryLimit vs. rollback | Comparable | Both correct, both quote source text directly. |
| Q9 | StorageClass binding mode vs. topology | **SMOS better** | Both reached the correct answer, but baseline appended "(inferred from standard Kubernetes patterns)" — a self-flagged admission it wasn't confident this came from the retrieved text. SMOS's answer reads as grounded in an actual retrieved phrase, no such hedge. |
| Q10 | Ordinal termination vs. PDB drain time | Comparable | Both correct, both well-grounded, both cite the same mechanism precisely. |

**Tally:** SMOS better on 4 (Q2, Q3, Q7, Q9), baseline better on 2 (Q1, Q5), comparable on 4
(Q4, Q6, Q8, Q10).

## Conclusion

On raw quality tally, SMOS's retrieval edges out raw file reading (4 wins vs. 2, plus 4 ties) at
under a third of the token cost — a genuinely strong result for the core value proposition this
benchmark set out to test. But Q1's hallucination is a real, separate finding: SMOS's compressed
summary contained the right fact, and the subagent still stated the opposite conclusion in its
final answer. That's not a retrieval failure (the right text was surfaced) — it's a synthesis
failure downstream of retrieval, and token savings don't offset getting a factual question
backwards. Whether this is a one-off (small sample — 10 questions, no repeated trials, no
statistical power) or a pattern worth investigating (does compression/summarization at query
time introduce this kind of flip more often than reading raw text does?) is not something this
single run can answer.

**Threats to validity (§7 of the design spec):** none of the 10 questions showed obvious signs
of being answered from parametric Kubernetes knowledge rather than retrieved/read content — both
runs cited specific retrieved text for every answer, and Q9's baseline hedge ("inferred from
standard patterns") is if anything evidence the subagent was self-monitoring for this rather than
silently guessing.

**Not re-validated:** the MCP-protocol path itself (tool registration, permission scoping,
transport overhead) — this run tested SMOS's retrieval mechanism, not its MCP integration,
because no MCP server was available to test against. If SMOS is installed as an MCP server later,
rerunning Task 5 through the real MCP tools (rather than `smos_tools.py`) would close this gap
and also give a fair wall-clock comparison (a resident server, no per-call cold start).

## Recommendation for next time this benchmark runs

1. Fix the `reconstruct()` guidance bug found in this run — it needs a document/section graph
   node id, not a `semantic_query`/`recall` source UUID (segments and their parent
   document/section are different graph nodes).
2. If SMOS gets installed as an MCP server, rerun Task 5 for real end-to-end MCP-path numbers
   and a fair wall-clock comparison.
3. A 10-question, single-trial sample can't distinguish "SMOS hallucinates on synthesis
   sometimes" from "this one question was unlucky" — a larger or repeated-trial run would be
   needed to treat Q1's finding as more than a flagged anecdote.
