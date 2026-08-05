# EU-law toolchain benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether an agent answering questions over a real EU-law corpus via SMOS's
semantic-query/recall tools spends meaningfully fewer tokens than an agent answering via raw
file reads, at comparable quality — and whether `LegalAdapter`'s cross-reference graph-rescue,
proven so far only on synthetic bench text, holds up on real, unmodified EU legislative text.

**Architecture:** A 13-article EU-law corpus (Regulation (EU) 2018/1807 in full, 9 articles;
GDPR Chapter I, 4 articles) lives in `sandbox/eu-law-benchmark/corpus/` (gitignored, pulled
from live EUR-Lex HTML). It is ingested once into an isolated `.smos/` store under that same
directory via a deterministic Python script reusing `markdown_to_payload()` +
`do_ingest_document_schema(..., adapter=LegalAdapter())` — the same reference pattern
`tests/test_legal_adapter.py` already proves end-to-end, just on real text instead of a
2-provision fixture. Two subagents then answer the same 10 pre-written, cross-document
(GDPR ↔ 2018/1807) synthesis questions: a baseline `Agent`-tool dispatch reading the raw corpus
files, and a headless `claude -p` subprocess (cwd = the sandbox directory, so it gets its own
SMOS MCP server bound to the isolated store) using only SMOS tools. Token usage is read from
each run's own usage report. The orchestrator scores both answer sets against hand-written
reference answers, verified against the real EUR-Lex text during this plan's own writing.

**Tech Stack:** Python (`requests` + `bs4`, both already present in this environment, used only
by this sandbox script — not added to `pyproject.toml`) for corpus extraction;
`smos.ingest.transformers.markdown_transformer.markdown_to_payload` +
`smos.ingest.adapters.legal.LegalAdapter` + `smos.tools.ingest_tools.do_ingest_document_schema`
for ingestion; Claude Code CLI headless mode (`claude -p --output-format json`) for the isolated
SMOS-tool run; the `Agent` tool for the baseline run.

## Global Constraints

- No SMOS or `LegalAdapter` production code changes — measurement exercise against the system
  as it stands post-5.5b (spec §2).
- No blanket `--dangerously-skip-permissions` for the headless run — scope `--allowedTools` to
  exactly the tools needed.
- Corpus/store/payload data stays gitignored (already covered by the existing
  `sandbox/*/corpus/`, `sandbox/*/.smos/`, `sandbox/*/*.txt`, `sandbox/*/*.json` rules in
  `.gitignore` — verified present, no changes needed for this plan).
- 10 questions, every one requiring synthesis across both regulations — no single-hop lookups,
  and no question answerable from GDPR alone (spec §7 threat-to-validity mitigation).
- Corpus is English-language EUR-Lex text, not Hungarian (spec amendment 2026-08-05 — `LegalAdapter`'s
  regex is English-only; see `docs/superpowers/specs/2026-08-05-eu-law-benchmark-design.md` §4.1).

---

### Task 1: Corpus sourcing from live EUR-Lex

**Files:**
- Create: `sandbox/eu-law-benchmark/corpus/reg_2018_1807.txt` (gitignored)
- Create: `sandbox/eu-law-benchmark/corpus/gdpr_articles_1-4.txt` (gitignored)
- Create: `sandbox/eu-law-benchmark/corpus/SOURCE.md` (gitignored)
- Create: `sandbox/eu-law-benchmark/scripts/pull_corpus.py` (tracked)

**Interfaces:**
- Produces: `sandbox/eu-law-benchmark/corpus/*.txt`, consumed by Task 3 (ingestion) and Task 4
  (baseline subagent).

**Verified during plan-writing (do not re-derive):** EUR-Lex serves each regulation's *entire*
consolidated text from one URL (`https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:<celex>`)
— not one URL per article, contradicting the design spec §4.2's original per-article-fetch
assumption. So this task makes exactly 2 HTTP requests total (one per regulation), trivially
respecting `robots.txt`'s `Crawl-delay: 10`. Each article lives in
`<div class="eli-subdivision" id="art_N">...</div>` (verified against the live page for both
CELEX:32018R1807 — 9 such divs, ids `art_1`..`art_9` — and CELEX:32016R0679 — 99 such divs).
`div.get_text(separator="\n", strip=True)` on that div yields text starting with the literal
string `"Article N"` (from the `<p class="oj-ti-art">Article N</p>` heading), which is exactly
what `LegalAdapter._PROVISION_RE` needs to find a boundary.

**Known, harmless corpus artifact (verified, not a bug in this script):** EUR-Lex's own server
sends the curly quotes around GDPR's quoted defined terms (e.g. `'personal data'`) as literal
U+FFFD replacement characters (`�`) — confirmed by inspecting the raw response bytes directly,
not a client-side decoding error. The surrounding text is unaffected and fully readable. Leave
as-is; do not attempt to "fix" the encoding (there is nothing to fix — this is what the server
sends).

- [ ] **Step 1: Write the corpus-pull script**

Create `sandbox/eu-law-benchmark/scripts/pull_corpus.py`:

```python
"""Pulls Regulation (EU) 2018/1807 (in full) and GDPR Articles 1-4 from live
EUR-Lex into corpus/. Each regulation's full consolidated text lives at one
URL (verified 2026-08-05 — not one URL per article), so this makes exactly 2
HTTP requests, sleeping 10s between them per eur-lex.europa.eu/robots.txt's
Crawl-delay. Reusable under EUR-Lex's CC BY 4.0 legislative-text reuse policy
(Decision 2011/833/EU) — see SOURCE.md for the attribution this script writes.

Re-run any time to refresh the corpus; overwrites existing files.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0 (SMOS eu-law-benchmark research fetch)"}
_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
_ART_DIV_ID_RE = re.compile(r"^art_\d+$")


def _fetch_articles(celex: str, keep: set[int] | None) -> tuple[str, list[int]]:
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    parts: list[str] = []
    found: list[int] = []
    for div in soup.find_all("div", id=_ART_DIV_ID_RE, class_="eli-subdivision"):
        num = int(div["id"].split("_", 1)[1])
        if keep is not None and num not in keep:
            continue
        parts.append(div.get_text(separator="\n", strip=True))
        found.append(num)
    return "\n\n".join(parts), found


def main() -> None:
    _CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    text_1807, found_1807 = _fetch_articles("32018R1807", keep=None)
    (_CORPUS_DIR / "reg_2018_1807.txt").write_text(text_1807, encoding="utf-8")
    print(f"Reg 2018/1807: wrote articles {found_1807}")

    time.sleep(10)  # robots.txt Crawl-delay: 10

    text_gdpr, found_gdpr = _fetch_articles("32016R0679", keep={1, 2, 3, 4})
    (_CORPUS_DIR / "gdpr_articles_1-4.txt").write_text(text_gdpr, encoding="utf-8")
    print(f"GDPR: wrote articles {found_gdpr}")

    (_CORPUS_DIR / "SOURCE.md").write_text(
        "# Corpus source\n\n"
        "Pulled from EUR-Lex (eur-lex.europa.eu), English-language text:\n"
        "- Regulation (EU) 2018/1807 on the free flow of non-personal data, "
        "CELEX 32018R1807, Articles 1-9 (full text).\n"
        "- Regulation (EU) 2016/679 (GDPR), CELEX 32016R0679, Articles 1-4 "
        "(Chapter I, General provisions) only.\n\n"
        "Reuse: permitted for commercial or non-commercial purposes under "
        "Decision 2011/833/EU, CC BY 4.0 (source acknowledgement required) — "
        "see https://eur-lex.europa.eu/content/legal-notice/legal-notice.html.\n\n"
        "Re-pull with: sandbox/eu-law-benchmark/scripts/pull_corpus.py\n",
        encoding="utf-8",
    )
    print(f"\nWrote 2 corpus files + SOURCE.md into {_CORPUS_DIR}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and verify**

```bash
python sandbox/eu-law-benchmark/scripts/pull_corpus.py
```

Expected output: `Reg 2018/1807: wrote articles [1, 2, 3, 4, 5, 6, 7, 8, 9]` then, after a 10s
pause, `GDPR: wrote articles [1, 2, 3, 4]`, then the final "Wrote 2 corpus files..." line.

```bash
grep -c "^Article " sandbox/eu-law-benchmark/corpus/reg_2018_1807.txt
grep -c "^Article " sandbox/eu-law-benchmark/corpus/gdpr_articles_1-4.txt
```

Expected: `9` and `4` respectively (one `Article N` heading line starting each article's text).
Spot-check `gdpr_articles_1-4.txt` contains the substring `Article 4` followed by `Definitions`
and, further down, `personal data` (the `�`-mangled quotes around it are expected — see the
"Known, harmless corpus artifact" note above, not a bug to chase).

- [ ] **Step 3: Commit**

```bash
git add sandbox/eu-law-benchmark/scripts/pull_corpus.py
git commit -m "feat: add EUR-Lex corpus-pull script for EU-law toolchain benchmark"
```

(`corpus/*.txt` and `SOURCE.md` stay gitignored per the existing `sandbox/*/corpus/`,
`sandbox/*/*.txt` rules in `.gitignore` — nothing further to add there.)

---

### Task 2: Question set + reference answers

**Files:**
- Create: `sandbox/eu-law-benchmark/questions.md` (tracked)

**Interfaces:**
- Consumes: `sandbox/eu-law-benchmark/corpus/*.txt` (Task 1).
- Produces: the 10-question set + reference answers Tasks 4/5 dispatch against and Task 6 scores
  against.

Every fact in the 10 questions below was verified against the real, live EUR-Lex text for both
regulations during this plan's writing (2026-08-05) — quoted phrases are copied from the actual
articles; paraphrased facts were cross-checked against the same source. Re-verify against the
files Task 1 actually wrote before treating this as final (Step 2 below) — Task 1 uses a
different extraction path (raw HTML → `BeautifulSoup.get_text()`) than the fetch used to draft
these questions, so a word-level diff is worth a quick look even though the underlying legal
text is identical either way.

- [ ] **Step 1: Write the question set**

Create `sandbox/eu-law-benchmark/questions.md`:

```markdown
# EU-law benchmark — question set

10 questions, each requiring synthesis across Regulation (EU) 2018/1807 AND GDPR — no question
is answerable from GDPR alone (threat-to-validity mitigation, design spec §7: GDPR alone is
too well-known to any LLM to test retrieval; Regulation 2018/1807 is the low-memorization
anchor). Reference answers grounded in the real EUR-Lex text, not shown to either subagent —
used only for scoring in Task 6.

## Q1 — Pseudonymised identifiers in a mixed dataset
**Articles:** Reg 2018/1807 Art 2(2), Art 3(1); GDPR Art 4(1), Art 4(5)
**Question:** A company's dataset mixes anonymized sensor readings with pseudonymised user
identifiers that the company can still re-link via a separate key it holds. Regulation
2018/1807 Article 3(1) defines "data" (its own scope) as data other than personal data as
defined in GDPR Article 4(1); GDPR Article 4(5) defines pseudonymisation as processing personal
data so it can no longer be attributed to a data subject "without the use of additional
information." Given the company still holds that additional information, does the
pseudonymised-identifier portion of the dataset count as 2018/1807's "data," and which regime
governs the dataset as a whole?
**Reference answer:** Because the company retains the key needed to re-link the identifiers to
individuals, GDPR Article 4(5)'s own definition means the pseudonymised identifiers remain
"personal data" under GDPR Article 4(1) — pseudonymisation doesn't remove personal-data status,
it's still attributable via additional information the company holds. So the identifiers fall
outside 2018/1807 Article 3(1)'s definition of "data" (which explicitly excludes personal
data). Since the sensor readings and the identifiers are inextricably linked in one dataset,
Article 2(2)'s second sentence applies: 2018/1807 "shall not prejudice the application of
Regulation (EU) 2016/679" — GDPR governs the whole linked dataset, not just 2018/1807's
non-personal-data rules.

## Q2 — Scope of the data-localisation prohibition
**Articles:** Reg 2018/1807 Art 3(1), Art 4(1); GDPR Art 4(1)
**Question:** Regulation 2018/1807 Article 4(1) prohibits Member State data-localisation
requirements unless justified by public security, but only for "data" as Article 3(1) defines
the term. If a Member State law instead requires in-country storage specifically for datasets
containing personal data (as GDPR Article 4(1) defines it), is that law covered by Article
4(1)'s prohibition?
**Reference answer:** No. Article 3(1) defines "data" for 2018/1807's purposes as data other
than personal data under GDPR Article 4(1); Article 4(1)'s prohibition is scoped to that same
defined term. A localisation requirement aimed at personal data falls outside 2018/1807
entirely — it isn't a "data localisation requirement" this Regulation prohibits or permits;
whether it's lawful is a question for GDPR's own rules, not 2018/1807 Article 4.

## Q3 — Competent-authority access vs. the mixed-dataset carve-out
**Articles:** Reg 2018/1807 Art 5(1), Art 2(2)
**Question:** 2018/1807 Article 5(1) says access to "data" by competent authorities may not be
refused merely because it's processed in another Member State. If the requested dataset is a
mixed personal/non-personal dataset with the two parts inextricably linked, does Article
5(1)'s no-refusal guarantee extend to the personal-data portion?
**Reference answer:** Not directly. Article 5(1)'s guarantee is scoped to "data" as Article
3(1) defines it (non-personal data), and Article 2(2) states that for inextricably-linked mixed
data, 2018/1807 "shall not prejudice" GDPR. So the personal-data portion of the linked dataset
is governed by GDPR's own access/legal-basis rules, not by Article 5(1)'s guarantee — a
competent authority seeking that portion needs a GDPR-compliant basis, separate from (and not
automatically granted by) 2018/1807's Article 5(1).

## Q4 — Porting codes of conduct vs. the personal-data portion
**Articles:** Reg 2018/1807 Art 6(1)(a), Art 2(2); GDPR Art 1
**Question:** 2018/1807 Article 6(1)(a) encourages self-regulatory codes of conduct for porting
data between cloud service providers in machine-readable formats. If a professional user asks
a provider to port a mixed dataset under such a code, does that code of conduct, on its own,
satisfy the provider's obligations for the personal-data portion of that dataset?
**Reference answer:** No. Article 6's codes of conduct are a 2018/1807 mechanism aimed at data
as Article 3(1) defines it (non-personal data); GDPR Article 1 states GDPR's own subject matter
is "rules relating to the protection of natural persons with regard to the processing of
personal data" — a separate, mandatory legal regime a voluntary porting code can't substitute
for. Article 2(2)'s "shall not prejudice" language confirms 2018/1807's porting mechanism
doesn't override GDPR obligations attaching to the personal-data portion.

## Q5 — Cross-border cooperation channel vs. GDPR's own territorial regime
**Articles:** Reg 2018/1807 Art 7, Art 2(2); GDPR Art 3
**Question:** 2018/1807 Article 7 sets up a single-point-of-contact procedure for one Member
State's competent authority to request non-personal data held in another Member State. Is this
the right channel for a competent authority seeking the personal-data portion of a mixed
dataset from a controller established in another Member State?
**Reference answer:** No — Article 7's procedure is scoped to "data" under Article 3(1)
(non-personal data); per Article 2(2), the personal-data portion of an inextricably-linked
mixed dataset stays under GDPR. GDPR Article 3 already establishes its own territorial-scope
rules for controllers/processors established in the EU (or targeting EU data subjects)
regardless of where processing occurs — that GDPR framework, not 2018/1807 Article 7's
cooperation procedure, governs access to the personal-data portion.

## Q6 — Public-security exception doesn't cross the personal/non-personal boundary
**Articles:** Reg 2018/1807 Art 4(1); GDPR Art 2
**Question:** 2018/1807 Article 4(1) allows a Member State to keep a data-localisation
requirement for non-personal data if justified on public-security grounds. Does an equivalent
public-security carve-out remove the personal-data portion of a mixed dataset from GDPR's own
material scope?
**Reference answer:** No — GDPR Article 2's material-scope exclusions are narrow (e.g., purely
personal/household processing) and do not include a general public-security carve-out matching
2018/1807 Article 4(1)'s. So even where a public-security justification lets a Member State
keep a localisation rule for the non-personal part under 2018/1807, the personal-data portion
of the same mixed dataset stays fully within GDPR's material scope — the two Regulations'
public-security/exclusion logic doesn't transfer across the personal/non-personal boundary.

## Q7 — Why deanonymisation risk matters to a "non-personal data" Regulation
**Articles:** Reg 2018/1807 Art 8(1)(a); GDPR Art 4(5)
**Question:** 2018/1807 Article 8(1)(a) requires the Commission to evaluate the Regulation's
application to mixed datasets "considering deanonymisation risks." Why would deanonymisation
risk matter for a Regulation that, per Article 3(1), only ever applies to non-personal data in
the first place?
**Reference answer:** Because whether data is genuinely "non-personal" (and thus within
2018/1807's own scope) depends on whether it can be re-attributed to a person — the same
question GDPR Article 4(5)'s pseudonymisation definition turns on ("can no longer be attributed
to a specific data subject without the use of additional information"). Data companies treat as
anonymised/non-personal under 2018/1807 may in fact be re-identifiable, meaning it was
mischaracterized and should have been treated as personal data under GDPR all along; Article
8(1)(a)'s review exists to check how large that mischaracterization risk is in practice.

## Q8 — What the mandated GDPR-interaction guidance adds beyond Article 2(2)
**Articles:** Reg 2018/1807 Art 2(2), Art 8(1)(a), Art 8(3)
**Question:** Article 2(2) already states the substantive rule for mixed datasets (non-personal
part under 2018/1807, GDPR unaffected for inextricably-linked personal data). Article 8(3)
separately requires the Commission to publish guidance on "the interaction of this Regulation
and Regulation (EU) 2016/679, especially as regards data sets composed of both personal and
non-personal data" by 29 May 2019. What open question is that guidance meant to resolve, given
Article 2(2) already sets the rule?
**Reference answer:** Article 2(2) states the rule but not how to apply it in practice —
determining whether a given dataset's personal and non-personal parts are "inextricably
linked" (versus separable) is exactly the practical, fact-specific judgment call Article
8(1)(a)'s deanonymisation-risk review and Article 8(3)'s interaction guidance are meant to help
make; the Articles establish that a boundary exists and who it defers to (GDPR, for linked
data), not how to draw it for a specific dataset.

## Q9 — Two independently-triggered territorial regimes for one dataset
**Articles:** Reg 2018/1807 Art 1, Art 2(1); GDPR Art 3
**Question:** A non-EU cloud provider with no EU establishment stores a mixed dataset for EU
business customers, offering its storage service to those Union-based users. GDPR Article 3
extends GDPR to non-EU entities that offer goods or services to, or monitor, data subjects in
the Union. Does 2018/1807 apply an equivalent extraterritorial test to the non-personal part of
the same dataset?
**Reference answer:** Yes, on a similar footing — 2018/1807 Article 2(1) scopes the Regulation
to processing of non-personal data in the Union carried out as a service to users located in
the Union (as well as by persons established in the Union), so a non-EU provider serving
Union-based business customers falls within 2018/1807's own scope for the non-personal part, on
a service-to-EU-users basis analogous to (but a legally separate test from) GDPR Article 3's
offering-goods-or-services trigger for the personal-data part. The same dataset can therefore be
split across two independently-triggered territorial regimes.

## Q10 — Repeal deadline vs. the mixed-dataset carve-out
**Articles:** Reg 2018/1807 Art 4(3), Art 2(2)
**Question:** Article 4(3) requires Member States to repeal existing data-localisation
requirements that don't meet Article 4(1)'s public-security justification, by 30 May 2021. Does
that repeal deadline apply to a Member State law requiring in-country storage of a dataset that
is an inextricably-linked mix of personal and non-personal data?
**Reference answer:** Not on its own terms. Article 4's repeal obligation runs against
localisation requirements for "data" under Article 3(1) — i.e., non-personal data. Because
Article 2(2) excludes inextricably-linked mixed data from 2018/1807's substantive rules (leaving
it to GDPR), a law requiring in-country storage of such a mixed dataset isn't a "data
localisation requirement" 2018/1807 Article 4 reaches at all, so the 30 May 2021 repeal deadline
doesn't apply to it by virtue of this Regulation — its lawfulness (if any) would need to be
assessed under GDPR or other applicable law instead.
```

- [ ] **Step 2: Re-verify against the files Task 1 actually wrote**

```bash
grep -n "Article 2\|Article 3\|Article 4\|Article 5\|Article 6\|Article 7\|Article 8" sandbox/eu-law-benchmark/corpus/reg_2018_1807.txt | head -20
grep -n "Article 1\|Article 2\|Article 3\|Article 4" sandbox/eu-law-benchmark/corpus/gdpr_articles_1-4.txt | head -10
```

Read enough of both files to confirm every quoted phrase in `questions.md` (e.g. `"shall not
prejudice the application of Regulation (EU) 2016/679"`, `"data localisation requirements shall
be prohibited"`) appears verbatim. If any quote or fact doesn't match what Task 1's script
actually pulled, fix `questions.md` before moving on — don't let plan-time and execution-time
text silently diverge.

- [ ] **Step 3: Commit**

```bash
git add sandbox/eu-law-benchmark/questions.md
git commit -m "feat: add EU-law benchmark question set + reference answers"
```

---

### Task 3: Deterministic ingestion into the isolated sandbox store via `LegalAdapter`

**Files:**
- Create: `sandbox/eu-law-benchmark/scripts/ingest_corpus.py` (tracked)
- Create (generated, gitignored): `sandbox/eu-law-benchmark/.smos/faiss.index`,
  `sandbox/eu-law-benchmark/.smos/metadata.db`

**Interfaces:**
- Consumes: `sandbox/eu-law-benchmark/corpus/*.txt` (Task 1); `markdown_to_payload(text, system,
  native_id, doc_name=None) -> IngestionPayload` from
  `smos.ingest.transformers.markdown_transformer`; `LegalAdapter` from
  `smos.ingest.adapters.legal`; `do_ingest_document_schema(payload, store, adapter=None) ->
  dict` from `smos.tools.ingest_tools` (verified current signature: returns `{"status":
  "success", "doc_id", "segment_count", "segment_ids", "section_count", "reference_count",
  "resumed"}` on success, `{"status": "quarantined", "quarantine_key", "errors"}` on validation
  failure); `VectorStore(persist_path: Path)` from `smos.memory.vector_store`.
- Produces: a populated `.smos/` store at `sandbox/eu-law-benchmark/.smos/` — the directory a
  `smos-server` subprocess opens automatically when launched with that cwd (Task 5).

This reuses exactly the pattern `tests/test_legal_adapter.py::test_legal_cross_reference_graph_rescue_end_to_end`
already proves works: `markdown_to_payload()` (legal text has no `#` markdown headings, so it
all becomes body blocks — that's fine, `LegalAdapter.structural_boundaries()` finds its
`Article N` boundaries by scanning the concatenated full text directly, independent of
`ContentBlock` typing) then `do_ingest_document_schema(payload, store, adapter=LegalAdapter())`.
Passing `adapter=LegalAdapter()` explicitly is the one detail that makes this a real test of the
adapter — get it wrong (omit it, or pass `None`) and ingestion silently falls back to no
structural/reference extraction at all.

- [ ] **Step 1: Write the ingestion script**

Create `sandbox/eu-law-benchmark/scripts/ingest_corpus.py`:

```python
"""Ingest sandbox/eu-law-benchmark/corpus/*.txt into an isolated .smos/ store,
via do_ingest_document_schema(..., adapter=LegalAdapter()) -- the same
structure-preserving, reference-extracting path
tests/test_legal_adapter.py already proves end-to-end on a small fixture,
run here on real EUR-Lex text instead.

Run from anywhere; paths resolved relative to this file. Idempotent:
do_ingest_document_schema upserts by (system, native_id).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SANDBOX_DIR = _SCRIPT_DIR.parent
_CORPUS_DIR = _SANDBOX_DIR / "corpus"
_STORE_DIR = _SANDBOX_DIR / ".smos"
_REPO_ROOT = _SANDBOX_DIR.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

_DOCS = [
    ("reg_2018_1807.txt", "reg-2018-1807", "Regulation (EU) 2018/1807"),
    ("gdpr_articles_1-4.txt", "gdpr-articles-1-4", "GDPR Articles 1-4"),
]


def main() -> None:
    store = VectorStore(persist_path=_STORE_DIR)
    adapter = LegalAdapter()

    for filename, native_id, doc_name in _DOCS:
        path = _CORPUS_DIR / filename
        if not path.exists():
            raise SystemExit(f"{path} not found -- run pull_corpus.py first")
        text = path.read_text(encoding="utf-8")
        payload = markdown_to_payload(
            text, system="eu-law-corpus", native_id=native_id, doc_name=doc_name
        )
        payload.domain_tags = ["eu-law"]
        result = do_ingest_document_schema(payload, store, adapter=adapter)
        print(
            f"{filename}: status={result.get('status')} "
            f"doc_id={result.get('doc_id')} "
            f"segments={result.get('segment_count')} "
            f"sections={result.get('section_count')} "
            f"references={result.get('reference_count')}"
        )
        if result.get("status") != "success":
            print(f"  WARNING non-success status, full result: {result}")

    print(f"\nIngested {len(_DOCS)} documents into {_STORE_DIR}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
python sandbox/eu-law-benchmark/scripts/ingest_corpus.py
```

Expected: two `status=success` lines, `reference_count` > 0 for `reg_2018_1807.txt` (its 9
articles cross-reference each other and GDPR — Article 3(1)'s citation of "Article 4 of
Regulation (EU) 2016/679" alone should produce at least one), final "Ingested 2 documents"
line. No `WARNING` lines.

- [ ] **Step 3: Verify store isolation**

```bash
ls -la sandbox/eu-law-benchmark/.smos/
ls -la .smos/ data/
```

Expected: `sandbox/eu-law-benchmark/.smos/metadata.db` exists, distinct file from
`.smos/metadata.db` / `data/metadata.db` at the repo root, which stay untouched (this script
only ever opens `_STORE_DIR = sandbox/eu-law-benchmark/.smos`).

- [ ] **Step 4: Spot-check structural boundaries actually fired**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from smos.memory.vector_store import VectorStore
store = VectorStore(persist_path='sandbox/eu-law-benchmark/.smos')
segs = store.get_segment_ids_by_doc('$(python -c "
import hashlib
print(hashlib.sha1(b'eu-law-corpus:reg-2018-1807').hexdigest()[:16])
")')
print(f'{len(segs)} segments for reg-2018-1807')
for sid in segs[:3]:
    print('---')
    print(store.get_by_uuid(sid)['content'][:200])
"
```

Expected: more than 1 segment (structural split by `Article N` occurred — a single 1-segment
result would mean `LegalAdapter.structural_boundaries()` found no matches, i.e. Task 1's
extraction didn't preserve `Article N` markers the way verified in Task 1). At least one printed
segment should start with `Article 1` or another `Article N` heading.

- [ ] **Step 5: Note the two real `LegalAdapter`-on-real-text findings for Task 6**

Two things verified during this plan's writing that Task 6's scoring must explicitly check for,
not silently ignore:

1. **GDPR's defined-term convention doesn't match `_DEFINING_PATTERN_RE`.** GDPR defines terms
   as `'personal data' means ...` — lowercase, single-quoted. `_DEFINING_PATTERN_RE` requires a
   Title-Case term (`[A-Z][A-Za-z]+...`) before `means`/`shall mean`. So the defining-sentence
   self-topic-triple mechanism (design spec §4 (b), first bullet) will **not** fire on GDPR's
   real definitions — only `_TITLE_CASE_PHRASE_RE`'s broader scan (catching phrases like "Member
   State", "Union", "Regulation (EU) 2016/679") contributes defined-term-style edges on this
   corpus. Expected, not a bug to chase — `_TITLE_CASE_PHRASE_RE` still gives cross-document
   graph connectivity, just via a different mechanism than the one the design spec's synthetic
   bench text exercises.
2. **Cross-regulation `Article N` collision risk.** `LegalAdapter`'s citation mechanism keys
   concept nodes by the bare matched string (e.g. `concept:article 4`), with no awareness of
   *which* regulation a citation refers to. 2018/1807's own Article 4 (data localisation) and a
   citation to "Article 4 of Regulation (EU) 2016/679" (GDPR's definitions article) inside
   2018/1807 Article 3(1) both resolve to the same `concept:article 4` node. This is a genuine
   ambiguity in `LegalAdapter` on a real multi-regulation corpus — worth reporting as an
   observation in Task 6, not something to fix here (out of scope per Global Constraints).

- [ ] **Step 6: Commit**

```bash
git add sandbox/eu-law-benchmark/scripts/ingest_corpus.py
git commit -m "feat: add LegalAdapter ingestion script for EU-law benchmark sandbox store"
```

---

### Task 4: Baseline run — raw `Read` subagent

**Files:**
- Create (gitignored): `sandbox/eu-law-benchmark/baseline_result.json`

**Interfaces:**
- Consumes: `sandbox/eu-law-benchmark/corpus/*.txt` (Task 1), question text from
  `sandbox/eu-law-benchmark/questions.md` (Task 2, question text only — not reference answers).
- Produces: `baseline_result.json` — raw answer text + usage metadata for Task 6.

- [ ] **Step 1: Dispatch the baseline subagent**

```
Agent({
  description: "EU-law benchmark baseline (raw Read)",
  subagent_type: "general-purpose",
  model: "haiku",
  run_in_background: false,
  prompt: `You are answering 10 questions about EU law using ONLY the Read and Glob tools --
do not use any MCP tools, do not answer from prior knowledge of GDPR or EU law you weren't able
to verify by reading these files. The corpus is 2 text files in
C:\\private\\semantic_memory\\sandbox\\eu-law-benchmark\\corpus\\: reg_2018_1807.txt
(Regulation (EU) 2018/1807, 9 articles) and gdpr_articles_1-4.txt (GDPR Articles 1-4 only).
Read whichever files you need per question.

Answer these 10 questions, one paragraph each, citing which file(s) and article number(s) you
drew the answer from:

1. A company's dataset mixes anonymized sensor readings with pseudonymised user identifiers
   that the company can still re-link via a separate key it holds. Regulation 2018/1807
   Article 3(1) defines "data" (its own scope) as data other than personal data as defined in
   GDPR Article 4(1); GDPR Article 4(5) defines pseudonymisation as processing personal data so
   it can no longer be attributed to a data subject "without the use of additional
   information." Given the company still holds that additional information, does the
   pseudonymised-identifier portion of the dataset count as 2018/1807's "data," and which
   regime governs the dataset as a whole?
2. Regulation 2018/1807 Article 4(1) prohibits Member State data-localisation requirements
   unless justified by public security, but only for "data" as Article 3(1) defines the term.
   If a Member State law instead requires in-country storage specifically for datasets
   containing personal data (as GDPR Article 4(1) defines it), is that law covered by Article
   4(1)'s prohibition?
3. 2018/1807 Article 5(1) says access to "data" by competent authorities may not be refused
   merely because it's processed in another Member State. If the requested dataset is a mixed
   personal/non-personal dataset with the two parts inextricably linked, does Article 5(1)'s
   no-refusal guarantee extend to the personal-data portion?
4. 2018/1807 Article 6(1)(a) encourages self-regulatory codes of conduct for porting data
   between cloud service providers in machine-readable formats. If a professional user asks a
   provider to port a mixed dataset under such a code, does that code of conduct, on its own,
   satisfy the provider's obligations for the personal-data portion of that dataset?
5. 2018/1807 Article 7 sets up a single-point-of-contact procedure for one Member State's
   competent authority to request non-personal data held in another Member State. Is this the
   right channel for a competent authority seeking the personal-data portion of a mixed
   dataset from a controller established in another Member State?
6. 2018/1807 Article 4(1) allows a Member State to keep a data-localisation requirement for
   non-personal data if justified on public-security grounds. Does an equivalent
   public-security carve-out remove the personal-data portion of a mixed dataset from GDPR's
   own material scope?
7. 2018/1807 Article 8(1)(a) requires the Commission to evaluate the Regulation's application
   to mixed datasets "considering deanonymisation risks." Why would deanonymisation risk
   matter for a Regulation that, per Article 3(1), only ever applies to non-personal data in
   the first place?
8. Article 2(2) already states the substantive rule for mixed datasets (non-personal part
   under 2018/1807, GDPR unaffected for inextricably-linked personal data). Article 8(3)
   separately requires the Commission to publish guidance on "the interaction of this
   Regulation and Regulation (EU) 2016/679, especially as regards data sets composed of both
   personal and non-personal data" by 29 May 2019. What open question is that guidance meant
   to resolve, given Article 2(2) already sets the rule?
9. A non-EU cloud provider with no EU establishment stores a mixed dataset for EU business
   customers, offering its storage service to those Union-based users. GDPR Article 3 extends
   GDPR to non-EU entities that offer goods or services to, or monitor, data subjects in the
   Union. Does 2018/1807 apply an equivalent extraterritorial test to the non-personal part of
   the same dataset?
10. Article 4(3) requires Member States to repeal existing data-localisation requirements that
    don't meet Article 4(1)'s public-security justification, by 30 May 2021. Does that repeal
    deadline apply to a Member State law requiring in-country storage of a dataset that is an
    inextricably-linked mix of personal and non-personal data?

Format your final response as numbered answers, one per question, each citing source
file(s)/article number(s).`
})
```

- [ ] **Step 2: Save the result**

Write the `Agent` tool's returned text and usage metadata to
`sandbox/eu-law-benchmark/baseline_result.json`:

```json
{
  "run": "baseline-raw-read",
  "model": "haiku",
  "answers_text": "<paste the Agent tool's returned message text verbatim>",
  "usage": "<paste the Agent tool's returned usage metadata verbatim>"
}
```

- [ ] **Step 3: Verify**

Read the saved file back, confirm all 10 answers are present and each cites at least one corpus
filename/article. If any answer looks like unverified prior GDPR knowledge with no real article
citation, note it — a Task 6 scoring input (spec §7/§4.6 specificity check), not a reason to
redo this task.

---

### Task 5: SMOS-tool run — isolated headless subagent

**Files:**
- Create (gitignored): `sandbox/eu-law-benchmark/smos_prompt.txt`,
  `sandbox/eu-law-benchmark/smos_result_raw.json`, `sandbox/eu-law-benchmark/smos_result.json`

**Interfaces:**
- Consumes: the populated `.smos/` store from Task 3, question text from
  `sandbox/eu-law-benchmark/questions.md` (Task 2).
- Produces: `smos_result.json` — same shape as Task 4's output, for Task 6.

- [ ] **Step 1: Write the prompt to a file**

Create `sandbox/eu-law-benchmark/smos_prompt.txt` (gitignored) with the same 10 questions as
Task 4's prompt (copy verbatim from Task 4 Step 1), but replace the instruction paragraph with:

```
You are answering 10 questions about EU law using ONLY the SMOS MCP tools
(tool_semantic_query, tool_recall, tool_reconstruct) -- do not use Read, do not answer from
prior knowledge of GDPR or EU law you weren't able to verify through a tool result. The corpus
has already been ingested into your SMOS memory store, tagged with domain "eu-law" -- query it,
don't assume you need to find or read any files.

[... same 10 numbered questions as Task 4 ...]

Format your final response as numbered answers, one per question, each mentioning which SMOS
tool call(s) surfaced the supporting content.
```

- [ ] **Step 2: Dispatch via headless `claude -p` from the sandbox cwd**

Isolation mechanism from spec §3/§4.3 — cwd must be `sandbox/eu-law-benchmark/` so the SMOS MCP
server (user-scope, cwd-bound at startup) opens *this* directory's `.smos/`, not the repo
root's:

```bash
cd sandbox/eu-law-benchmark
claude -p "$(cat smos_prompt.txt)" \
  --model haiku \
  --output-format json \
  --allowedTools "mcp__smos__tool_semantic_query mcp__smos__tool_recall mcp__smos__tool_reconstruct" \
  > smos_result_raw.json
cd ../..
```

- [ ] **Step 3: Verify the run actually used SMOS tools**

```bash
python -c "
import json
with open('sandbox/eu-law-benchmark/smos_result_raw.json') as f:
    data = json.load(f)
print('is_error:', data['is_error'])
print('num_turns:', data['num_turns'])
print('permission_denials:', data['permission_denials'])
print()
print(data['result'][:2000])
"
```

Expected: `is_error: False`, `num_turns` > 1, `permission_denials` empty, printed answer text
referencing specific article numbers/quoted phrases. `num_turns == 1` suggests the run answered
from pretrained knowledge without querying — flag in Task 6, don't silently rerun with looser
scoping.

- [ ] **Step 4: Extract usage and save the final result**

```bash
python -c "
import json
with open('sandbox/eu-law-benchmark/smos_result_raw.json') as f:
    data = json.load(f)
out = {
    'run': 'smos-tools-isolated',
    'model': 'haiku',
    'answers_text': data['result'],
    'usage': data['usage'],
    'total_cost_usd': data['total_cost_usd'],
    'num_turns': data['num_turns'],
}
with open('sandbox/eu-law-benchmark/smos_result.json', 'w') as f:
    json.dump(out, f, indent=2)
print('saved smos_result.json')
"
```

---

### Task 6: Score both runs, verify `LegalAdapter` mattered, write comparison

**Files:**
- Create: `docs/eu-law-benchmark-results.md` (tracked)
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: `sandbox/eu-law-benchmark/baseline_result.json` (Task 4),
  `sandbox/eu-law-benchmark/smos_result.json` (Task 5), reference answers in
  `sandbox/eu-law-benchmark/questions.md` (Task 2), the two `LegalAdapter` findings from Task 3
  Step 5.
- Produces: the benchmark's actual deliverable.

- [ ] **Step 1: Score each question**

For each of the 10 questions, the orchestrator (this session) reads the baseline answer, the
SMOS answer, and the reference answer side by side, records a one-line verdict (`comparable` /
`baseline better` / `smos better`, one-sentence reason), and flags whether either answer shows
signs of unverified parametric knowledge rather than corpus-grounded content (design spec §7) —
e.g. an answer that's generically GDPR-plausible but doesn't cite Regulation 2018/1807's actual
provisions, or gets its specific interaction wrong (details a model would only reliably get
right by reading the actual retrieved text, given 2018/1807's obscurity).

- [ ] **Step 2: Check whether `LegalAdapter`'s graph-rescue visibly mattered**

Separate from per-question scoring: did the SMOS-tool run's answers show evidence of the
citation/cross-reference graph connecting the two documents (e.g. retrieving GDPR Article 4(1)
content when queried about 2018/1807 Article 3(1), via the graph edge Task 3 confirmed exists),
or did it rely purely on vector/BM25 similarity without the graph contributing anything
observable? Report explicitly — this is goal 2 of the benchmark (design spec §1), not a side
note.

- [ ] **Step 3: Write the comparison doc**

Create `docs/eu-law-benchmark-results.md` with: total token usage per run (from the saved
`usage` blocks), the per-question verdict table from Step 1, the `LegalAdapter`-mattered verdict
from Step 2, the two real-corpus `LegalAdapter` findings from Task 3 Step 5 (GDPR's
lowercase-quoted defined-term convention not matching `_DEFINING_PATTERN_RE`; cross-regulation
`Article N` concept-id collision), and an overall conclusion (did SMOS win, lose, or wash on
this corpus, tying back to §7's validity threat if either run leaned on pretrained GDPR
knowledge rather than retrieval).

- [ ] **Step 4: Update `docs/HANDOFF.md`**

Add a pointer to the completed `docs/eu-law-benchmark-results.md` under "Where things stand,"
and restore the "Next up" framing to whichever of Track A' (5.5c) or Track B (Phase 4) makes
sense to pick up next.

- [ ] **Step 5: Commit**

```bash
git add docs/eu-law-benchmark-results.md docs/HANDOFF.md
git commit -m "docs: record EU-law benchmark results (SMOS tools vs raw reading, real LegalAdapter text)"
```

---

## Self-review notes

- **Spec coverage:** §4.1 corpus/reuse basis → Task 1 (with a documented refinement: English
  not Hungarian, per the 2026-08-05 spec amendment). §4.2 acquisition → Task 1 (with a
  documented refinement: 2 requests not ~40, per the real EUR-Lex page structure verified during
  planning). §3/§4.3 isolation → Task 5 Step 2. §4.4 ingestion → Task 3. §4.5 questions → Task 2
  (all 10 verified against real fetched text during planning, re-verification against Task 1's
  actual output required in Task 2 Step 2). §4.6 scoring → Task 6 Steps 1-2. §4.7 token
  measurement → Task 4 Step 2/Task 5 Step 4. §4.8 output → Task 6 Step 3. §5 edge cases → covered
  inline (Task 1 Step 2's expected-output check, Task 3 Step 4's boundary spot-check, Task 5 Step
  3's num_turns check). §6 testing → Task 3 Steps 3-4, Task 4 Step 3, Task 5 Step 3. §7 validity
  threat → baked directly into every question's design (Task 2) plus explicit checks in Task 6
  Step 1.
- **Deviations from the spec worth flagging explicitly (both discovered by reading real
  source/live pages during planning, not assumed):** (1) corpus language switched
  Hungarian→English — already captured as a formal spec amendment, committed separately
  (`7ab414e`) before this plan was written. (2) Acquisition mechanism simplified from "~40
  per-article requests" to "2 whole-regulation requests" after discovering EUR-Lex serves each
  regulation's full consolidated text from one URL — a strict simplification (same content,
  fewer requests, still fully respects `Crawl-delay: 10`), not a scope change, so handled as a
  plan-level refinement rather than another spec amendment. (3) GDPR corpus scope right-sized
  from the spec's original estimate ("~25-30 articles... territorial/material scope, cross-border
  transfer provisions") down to 4 articles (Chapter I, general provisions/definitions) after
  actually reading Regulation 2018/1807's real text and finding its only substantive GDPR
  cross-references are to Article 4(1)'s personal-data definition and the mixed-dataset
  provisions — not territorial scope or transfers. This is the spec's own §4.1 instruction being
  followed ("exact article list finalized after the text is pulled and read in full"), not a
  deviation from it.
- **Placeholder scan:** no TBD/TODO. Every question has a concrete, fact-checked reference
  answer; every script has real, tested-against-live-HTML extraction logic (verified via direct
  `requests`/`bs4` inspection during planning, not guessed).
- **Type/name consistency:** `markdown_to_payload`, `LegalAdapter`, `do_ingest_document_schema`
  (including its exact current return-dict keys — `status`/`doc_id`/`segment_count`/
  `segment_ids`/`section_count`/`reference_count`/`resumed`, verified by reading
  `smos/tools/ingest_tools.py:623-742` directly, not assumed from the k8s plan's slightly
  different recollection of this function's status values), `VectorStore(persist_path=...)`
  signatures in Task 3 match current source. `tool_semantic_query`/`tool_recall`/
  `tool_reconstruct` MCP tool names in Task 5 verified against `smos/server.py`.
