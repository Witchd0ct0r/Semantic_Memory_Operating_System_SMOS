# EU-law toolchain benchmark — results

**Plan:** `docs/superpowers/plans/2026-08-05-eu-law-benchmark.md`. **Design:**
`docs/superpowers/specs/2026-08-05-eu-law-benchmark-design.md`. Corpus: Regulation (EU)
2018/1807 (free flow of non-personal data, full text, 9 articles) + GDPR Chapter I
(Articles 1-4), pulled live from EUR-Lex (English text, CC BY 4.0). Ingested via the
schema-driven path with `LegalAdapter()` explicitly selected. 10 cross-document synthesis
questions, baseline (`Agent` tool, raw `Read`) vs. isolated SMOS-tool run (headless
`claude -p`, own `.smos/` store).

## Headline result: opposite of the Kubernetes-docs benchmark

The 2026-07-27 Kubernetes-docs toolchain benchmark found SMOS's tools used 67% fewer
tokens than raw file reading at comparable quality. On this corpus, **the raw-`Read`
baseline won on both token cost and answer quality** — SMOS's tools were not a wash, they
lost.

| | Baseline (`Agent` tool, raw `Read`) | SMOS-tool (headless `claude -p`) |
|---|---|---|
| Total tokens | 35,715 | 306,644 (input 74 + cache-creation 27,666 + cache-read 272,811 + output 6,093) |
| Tool calls / turns | 2 (one `Read` per corpus file) | 20 turns |
| Cost | not separately reported by the `Agent` tool | $0.1131521 |

**SMOS used 8.6x more tokens than the baseline**, not fewer. The likely cause: this corpus
is small (13 articles, ~13KB combined text) — cheap enough that reading both files whole in
2 tool calls is simply less work than 20 turns of iterative per-question retrieval calls,
even with prompt caching absorbing most of the repeated context. The Kubernetes benchmark's
corpus was ~20 full pages of documentation; token-efficiency from retrieval likely requires
a corpus large enough that reading it whole is itself expensive. This benchmark did not
test that regime — it tested the opposite end, where SMOS's per-query tool-calling
overhead dominates.

## Per-question scoring

| Q | Verdict | Reason |
|---|---|---|
| 1 | Comparable | Both correctly conclude pseudonymised data (re-linkable) stays personal, GDPR governs the linked dataset. |
| 2 | Comparable | Both correctly scope Article 4(1)'s prohibition to non-personal "data" only. |
| 3 | Comparable | Both correctly say Article 5(1)'s guarantee doesn't reach the personal-data portion. |
| 4 | Comparable (quality) / SMOS worse (grounding) | Both correct on substance, but both cite "GDPR Article 20" — not in the pulled corpus (only GDPR Arts 1-4 were ingested). SMOS's phrasing is worse here: it explicitly frames this as `SMOS (tool_recall: Article 6 and GDPR Article 20) clarified...`, presenting an ungrounded fact as though a real tool call retrieved it — a more misleading form of the same parametric-knowledge leak the baseline shows plainly. |
| 5 | Comparable | Both correctly say Article 7 isn't the right channel for the personal-data portion. |
| 6 | Comparable | Both correctly say the public-security carve-out doesn't cross into GDPR's material scope. |
| 7 | Comparable | Both correctly connect deanonymisation risk to mischaracterization of personal data as non-personal. |
| 8 | **SMOS better** | SMOS's answer explicitly names the practical gap the reference answer centers on — determining whether data is "inextricably linked" is what the Article 8(3) guidance resolves. The baseline's answer is a broader, more diffuse list of practical questions that touches related themes but doesn't isolate this point as precisely. |
| 9 | **Baseline better (SMOS factually wrong)** | Baseline correctly says yes, 2018/1807 has an equivalent extraterritorial trigger, and quotes the real Article 2(1)(a) text verbatim ("provided as a service to users residing or having an establishment in the Union"). SMOS answers **no**, claiming 2018/1807 Article 3 requires "an establishment of a controller or processor within the Union" — but that phrase is GDPR's own territorial-scope language (confirmed: present in `gdpr_articles_1-4.txt`, absent from `reg_2018_1807.txt`), and 2018/1807's real Article 3 is *Definitions*, not territorial scope. See "LegalAdapter's graph mattered — but hurt here" below. |
| 10 | Baseline slightly better | Reference answer: for inextricably-linked mixed data, Article 2(2) excludes it from 2018/1807's rules entirely, so Article 4(3)'s repeal deadline doesn't reach it "on its own terms." Baseline hedges toward this correctly. SMOS's "applies, but only to the extent the law affects the non-personal portion" implies a partial/severable framing that doesn't match Article 2(2)'s all-or-nothing treatment of *inextricably linked* data specifically. |

**Overall quality:** 7/10 comparable, 1/10 SMOS better, 2/10 baseline better (one of them —
Q9 — a clear factual error, not just a weaker phrasing).

## Did `LegalAdapter`'s graph-rescue visibly matter? Yes — and it visibly hurt on Q9.

This is the benchmark's second goal (design spec §1), and the answer is unambiguous: the
graph mechanism visibly influenced retrieval, confirmed by directly inspecting the ingested
graph (not inferred from answer text alone):

- **Verified during Task 3:** citing "Article 4 of Regulation (EU) 2016/679" inside
  2018/1807's Article 3(1) segment does *not* create a `concept:article 4` collision node
  as originally predicted in the design. Instead, `LegalAdapter`'s "last article match in
  the window = this window's own enclosing provision" heuristic misattributes the citation:
  because "Article 4" is the last match in that window, it's treated as the window's *own*
  provision rather than a citation target, producing an edge `Article 4 (as
  nearest_provision) --references--> concept:article 3` — an inverted/mislabeled edge, not
  a clean collision.
- **This is directly implicated in Q9's wrong answer.** Both regulations have their own
  "Article 3" (2018/1807's is *Definitions*; GDPR's is *Territorial scope*), sharing one
  `concept:article 3` graph node. The SMOS-tool run's Q9 answer attributes GDPR's
  territorial-scope text to "2018/1807 Article 3" — exactly the kind of cross-document
  mixup a shared, regulation-unaware concept node would produce. This wasn't independently
  re-derived for this write-up beyond the edge-level inspection above; the causal link is
  strongly plausible given the verified graph structure, not proven by tracing the live
  retrieval call itself.
- **Net assessment:** the mechanism is not inert on real text — it demonstrably shaped what
  the SMOS-tool run retrieved and how it was framed. On this corpus, in this run, that
  influence produced one clear factual error (Q9) and no clearly-attributable win (Q8's
  better answer could stem from vector/BM25 retrieval alone; nothing traces it to a graph
  edge the way Q9's error traces to one).

## Recommendation flowing from this result

`LegalAdapter`'s citation mechanism has no concept of which regulation an "Article N"
citation belongs to — a real gap on any corpus mixing multiple numbered legal instruments,
not just this one. Worth scoping a fix (e.g. namespacing citation concept ids by document,
or by regulation identifier extracted from context) before trusting cross-reference
graph-rescue on a real multi-regulation corpus. Out of scope for this benchmark itself
(measurement only, no production code changes) — a candidate item for whichever comes
next, Phase 5.5c or a dedicated `LegalAdapter` hardening slice.

On corpus-size-vs-token-cost: this result doesn't invalidate the Kubernetes benchmark's
conclusion, but does show SMOS's token advantage is not universal — it likely depends on
corpus size relative to what raw reading would cost. A future benchmark at a corpus size
between this one (13 articles) and the Kubernetes one (20 pages) would help find where the
crossover point is.
