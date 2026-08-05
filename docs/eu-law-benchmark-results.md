# EU-law toolchain benchmark — results

**Plan:** `docs/superpowers/plans/2026-08-05-eu-law-benchmark.md`. **Design:**
`docs/superpowers/specs/2026-08-05-eu-law-benchmark-design.md`. Corpus: Regulation (EU)
2018/1807 (free flow of non-personal data, full text, 9 articles) + GDPR Chapter I
(Articles 1-4), pulled live from EUR-Lex (English text, CC BY 4.0). Ingested via the
schema-driven path with `LegalAdapter()` explicitly selected. 10 cross-document synthesis
questions, three runs: baseline (`Agent` tool, raw `Read`), SMOS via a real MCP-tool
conversation (headless `claude -p`), and SMOS via direct Python calls (added after the
first write-up — see "Mechanism caveat" below for why).

## Mechanism caveat — read this before the numbers

The first version of this write-up compared this benchmark's SMOS run against the
2026-07-27 Kubernetes-docs benchmark's SMOS run as if they measured the same thing. They
didn't. The Kubernetes benchmark's "SMOS-tool run" never used the MCP tool surface —
no `smos` MCP server was installed on that machine at the time, so it substituted a
script (`sandbox/toolchain-benchmark/scripts/smos_tools.py`) calling the underlying
Python functions (`semantic_query()`, `recall()`, `reconstruct()`) directly, driven via
`Bash` from a single in-session `Agent` dispatch. This benchmark's original SMOS run
used the real thing: a headless `claude -p` process making actual MCP tool calls
(`tool_semantic_query`, `tool_recall`, `tool_reconstruct`) in one continuous 20-turn
agentic conversation.

That's an architecture difference with a large, mechanical token-cost consequence,
unrelated to retrieval quality or domain: in a real multi-turn MCP conversation, every
new turn re-sends the entire accumulated conversation (all prior tool calls, their
results, and the model's own prior answers) back through the model. `cache_read_input_tokens`
in that run's usage (272,811 of 306,644 total tokens) is exactly that — repeatedly
billing for a growing transcript, turn after turn, compounding across all 10 questions
answered in one thread. A direct-Python-call run doesn't have that property the same
way. **This section adds a third run using the same direct-Python-call mechanism as the
Kubernetes benchmark**, so this benchmark now has an actual apples-to-apples comparison
point, and the original MCP-conversation run's numbers are kept and clearly labeled
rather than removed.

## Headline result

| | Baseline (`Agent`, raw `Read`) | SMOS (MCP conversation, headless `claude -p`) | SMOS (direct Python calls, `Agent` + `Bash`) |
|---|---|---|---|
| Total tokens | 35,715 | 306,644 | 41,447 |
| Tool calls / turns | 2 (one `Read` per file) | 20 turns | 24 tool calls |
| Cost | not separately reported | $0.1131521 | not separately reported |
| vs. baseline | — | **8.6x more** | **1.16x more** |

Once the mechanism is held constant with the Kubernetes benchmark's methodology, the
picture changes substantially: SMOS's direct-Python-call run used only 16% more tokens
than the baseline (41,447 vs. 35,715) — not the 8.6x the MCP-conversation run showed.
That's remarkably close to the Kubernetes benchmark's own direct-Python SMOS number
(41,869 tokens, 22 tool calls) despite a completely different corpus and domain,
suggesting the **mechanism**, not the corpus or domain, is the dominant driver of
SMOS's token cost profile — the real MCP multi-turn conversation's compounding
cache-read cost is specific to that mechanism, not a property of "SMOS's retrieval" in
general. The MCP-conversation run's 8.6x-more-tokens result stands as a real, measured
number for that specific mechanism (headless `claude -p` running a long single-threaded
conversation), but it should not be read as "SMOS's tools cost more than raw reading"
in general — a same-shape single-`Agent`-dispatch comparison (both baseline and
direct-Python SMOS use the `Agent` tool, not a separate long-running process) shows
much closer parity.

On this small corpus (13 articles, ~13KB), raw `Read` still wins on tokens either way —
just by 16%, not 8.6x, once the SMOS run's mechanism matches the baseline's rather than
adding a second, architecturally different tool.

## Per-question scoring

| Q | Baseline | SMOS (MCP) | SMOS (direct Python) | Notes |
|---|---|---|---|---|
| 1 | Correct | Correct | Correct | All three agree with the reference. |
| 2 | Correct | Correct | Correct | All three agree with the reference. |
| 3 | Correct | Correct | Correct | All three agree with the reference. |
| 4 | Correct, cites "GDPR Article 20" | Correct, cites "GDPR Article 20" as if tool-retrieved | Correct, cites "GDPR Article 20" | All three show the same parametric-knowledge leak — "GDPR Article 20" (data portability) is not in the pulled corpus (only GDPR Arts 1-4 ingested). Reproducible across all three mechanisms; not specific to SMOS or to conversational drift. |
| 5 | Correct | Correct | Correct | All three agree with the reference. |
| 6 | Correct | Correct | Correct | All three agree with the reference. |
| 7 | Correct | Correct | Correct | All three agree with the reference. |
| 8 | Correct but diffuse | **Precise** — explicitly names the "inextricably linked" determination as the open question | Correct but diffuse (similar to baseline) | Only the MCP run isolates the reference answer's exact point; direct-Python and baseline both give a broader, less targeted list. |
| 9 | **Correct** — quotes real Article 2(1)(a) text, says Yes | **Wrong** — misattributes GDPR's territorial-scope language to "2018/1807 Article 3," says No | **Correct** — says Yes, paraphrases Article 2(1)(a) accurately | See "Q9: same store, different mechanism, different answer" below — the most interesting single result in this benchmark. |
| 10 | Correct, hedged appropriately | Muddled — implies a severable/partial-applicability reading Article 2(2) doesn't support | Muddled in a similar way — also implies a partial/non-personal-only repeal scope | Both SMOS runs share a similar imprecision here that the baseline avoids; not obviously mechanism-dependent. |

**Tally:** MCP run: 8/10 correct (1 clear error on Q9, 1 muddled on Q10). Direct-Python
run: 9/10 correct (only Q10 muddled, matching MCP's issue there but not Q9's). Baseline:
9/10 correct, same single soft spot on Q10-adjacent hedging but got there more cleanly.

## Q9: same store, different mechanism, different answer

This is the most informative single data point in this benchmark. Both SMOS runs query
the *same* ingested store, built by the *same* ingestion script, containing the *same*
`LegalAdapter`-extracted graph — including the verified `concept:article 3` collision
node shared between GDPR's Article 3 (territorial scope) and 2018/1807's Article 3
(definitions), documented below. Yet the MCP-conversation run answered Q9 wrong and the
direct-Python-call run answered it right.

This means the graph collision is a real, necessary ingredient (verified independently
by inspecting `graph_nodes`/`graph_edges` directly — see below) but not, on its own, a
sufficient explanation for the wrong answer. The MCP run's 20-turn single conversation
answering all 10 questions in sequence gives more opportunity for a shared/ambiguous
concept id to get conflated with something from an earlier turn's context than a
same-store, single-query-per-call approach does. Put differently: the collision is a
latent defect in the graph; whether it surfaces as a wrong answer looks like it also
depends on how much accumulated conversational context is around when the ambiguous
node gets retrieved. Both factors are worth keeping in the recommendation below — fixing
the graph collision remains the more durable fix, but this result suggests conversational
accumulation is a compounding risk factor on top of it, not an independent variable.

## Did `LegalAdapter`'s graph-rescue visibly matter? Yes.

This is the benchmark's second goal (design spec §1), and the answer is unambiguous: the
graph mechanism visibly influenced retrieval, confirmed by directly inspecting the
ingested graph (not inferred from answer text alone):

- **Verified during Task 3:** citing "Article 4 of Regulation (EU) 2016/679" inside
  2018/1807's Article 3(1) segment does *not* create a `concept:article 4` collision node
  as originally predicted in the design. Instead, `LegalAdapter`'s "last article match in
  the window = this window's own enclosing provision" heuristic misattributes the citation:
  because "Article 4" is the last match in that window, it's treated as the window's *own*
  provision rather than a citation target, producing an edge `Article 4 (as
  nearest_provision) --references--> concept:article 3` — an inverted/mislabeled edge, not
  a clean collision.
- **This is directly implicated in the MCP run's Q9 wrong answer** (but not sufficient on
  its own to explain it — see above). Both regulations have their own "Article 3"
  (2018/1807's is *Definitions*; GDPR's is *Territorial scope*), sharing one
  `concept:article 3` graph node.
- **Net assessment:** the mechanism is not inert on real text — it demonstrably shaped
  what the MCP run retrieved and how it was framed. On this corpus, the one clearly
  traceable instance of it mattering was a factual error, and the direct-Python run's
  clean Q9 answer against the same underlying graph shows the collision alone doesn't
  guarantee a wrong answer — conversational context accumulation appears to be a
  contributing factor.

## Recommendation flowing from this result

`LegalAdapter`'s citation mechanism has no concept of which regulation an "Article N"
citation belongs to — a real gap on any corpus mixing multiple numbered legal instruments,
not just this one. Worth scoping a fix (e.g. namespacing citation concept ids by document,
or by regulation identifier extracted from context) before trusting cross-reference
graph-rescue on a real multi-regulation corpus. Out of scope for this benchmark itself
(measurement only, no production code changes) — a candidate item for whichever comes
next, Phase 5.5c or a dedicated `LegalAdapter` hardening slice.

On corpus-size-vs-token-cost: once mechanism is held constant (`Agent`-tool dispatch vs.
`Agent`-tool dispatch), this result doesn't show SMOS losing on tokens the way the first
version of this write-up concluded — it shows roughly at-parity cost (+16%) on a small
corpus, which is a much weaker and more mundane finding than "SMOS costs 8.6x more."
The 8.6x number is real but specific to the long-running-MCP-conversation mechanism, and
a fair test of *that* mechanism's cost would need to control for turn count and transcript
growth directly (e.g. a version of this benchmark that dispatches one fresh MCP-backed
call per question instead of one long conversation across all 10) — not attempted here.
A future benchmark at a corpus size between this one (13 articles) and the Kubernetes one
(20 pages) would still help find where the crossover point for retrieval's value lies,
independent of this mechanism question.
