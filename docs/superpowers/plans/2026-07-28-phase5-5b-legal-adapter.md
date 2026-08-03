# Phase 5.5b (slice 1) — Legal Adapter & Reference Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `LegalAdapter` — a `DomainAdapter` implementation for legal/regulatory prose — so cross-reference graph-rescue works on plain legal text the same way it already works on markdown (Phase 5.5a), closing the 0%-recall gap `tests/bench_legal_regulations.py` measures for unmarked prose.

**Architecture:** New file `smos/ingest/adapters/legal.py` implementing the existing `DomainAdapter` protocol (`smos/ingest/adapters/base.py`) — no interface changes. `structural_boundaries()` splits on top-level provision markers (`Section N`, `Article N`, `Rule N`, `§ N`). `extract_references()` emits two independent kinds of triples merged into one list: (a) citation cross-references between provision markers, and (b) heuristic defined-term matching (a "X means Y" defining sentence, plus any later occurrence of the same Title-Case phrase) — mirroring `MarkdownAdapter`'s heading self-topic trick, generalized from headings to legal defined terms. Selected explicitly per ingestion call (`adapter=LegalAdapter()`); no auto-detection. Proven via a new pytest e2e test (mirrors 5.5a's markdown proof exactly) and a new comparison arm in the existing `tests/bench_legal_regulations.py` standalone script.

**Tech Stack:** Python, `re` (stdlib), pytest. No new dependencies.

## Global Constraints

- **No changes to `smos/ingest/adapters/base.py`'s `DomainAdapter` protocol.** `LegalAdapter` implements it as-is, same as `MarkdownAdapter`.
- **`LegalAdapter.edge_types = frozenset({"references"})`** — matches `MarkdownAdapter` exactly. No new edge types this slice.
- **No changes to `smos/memory/entity_extractor.py`.** The fix lives entirely in `LegalAdapter.extract_references()`, following the exact pattern 5.5a used for `MarkdownAdapter`. The generic backtick/AST extractor in `VectorStore._update_graph()` keeps running on every stored segment regardless of domain — contributes nothing on legal prose today, continues to contribute nothing; pre-existing behavior, not a regression.
- **No changes to `smos/compression/context_builder.py`.** Its existing 2-hop `edge_types=["references"]` graph-expansion step already handles this shape; no retrieval-side change needed.
- **No changes to `smos/ingest/segmenter.py`.** `_wire_references()` and `_REFERENCE_LOOKBACK_CHARS` (300 chars) are reused unchanged — they already call `adapter.extract_references()` on a lookback window per leaf and wire `leaf.memory_id --edge_type--> concept:{target_name}`, ignoring the triple's `source_name` field. This plan's `extract_references()` implementation must return triples whose `edge_type` is always `"references"` (the adapter's only declared type) for `_wire_references()`'s `if edge_type not in valid_edge_types: continue` filter to pass.
- **Scope fence: this slice is adapter + reference extraction only.** Do NOT implement domain classification, typed traceability edges (`satisfies`/`verified_by`/`refines`/`allocated_to`/`complies_with`/`impacts`), the fan-out evidence-gathering primitives (`resolve`/`traverse`/`classify_domains`/`coverage_check`), the coverage/gap signal, multilingual embeddings, or PDF/DOCX/HTML client transformers — all explicitly deferred to 5.5c per the design spec's §6. Legal text is assumed to already arrive as plain/markdown-shaped text via the existing schema-ingestion path (`markdown_to_payload` is reused as-is for test fixtures and the bench script; no new client transformer is built).
- **Sub-clause lettering/numbering (`(a)(1)(i)`) is not separately spanned.** It falls through to the segmenter's semantic/size-fit cascade layers, exactly as markdown only structurally splits at heading level.
- **Accepted false-positive risk (documented in the design spec, not a defect):** the general Title-Case-phrase regex in `extract_references()` fires on any capitalized 2-4-word phrase, not only genuine defined terms. This is additive graph noise (extra low-value concept nodes), not a correctness bug. Not scheduled to be solved in this slice.
- **`tests/bench_legal_regulations.py` is a standalone script, NOT pytest-collected** (`pytest.ini` sets `python_files = ["test_*.py"]`). Verify its new comparison arm by running it directly: `python tests/bench_legal_regulations.py`.
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"**, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known local `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious — match the style already in `smos/ingest/adapters/markdown.py`.

---

### Task 1: `LegalAdapter` skeleton + structural grammar

**Files:**
- Create: `smos/ingest/adapters/legal.py`
- Test: `tests/test_legal_adapter.py`

**Interfaces:**
- Consumes: `DomainAdapter` protocol (`smos/ingest/adapters/base.py`, unchanged).
- Produces: `LegalAdapter` class — `name = "legal"`, `edge_types: frozenset[str] = frozenset({"references"})`, `structural_boundaries(text: str) -> list[tuple[int, int]]`, `llm_boundary_prompt() -> Optional[str]` (returns `None`), and a placeholder `extract_references(text: str) -> list[tuple[str, str, str]]` returning `[]` (real implementation lands in Task 2). Consumed by Task 2 (same file), Task 3 (e2e test), Task 4 (bench script).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_legal_adapter.py`:

```python
from __future__ import annotations

import pytest

from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.adapters.registry import all_edge_types, get_adapter, register_adapter, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_structural_boundaries_splits_on_provisions():
    adapter = LegalAdapter()
    text = "Section 1 requires retention.\nArticle 2 restricts transfer.\nRule 3-A.1 sets a deadline.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].startswith("Section 1")
    assert text[spans[1][0]:spans[1][1]].startswith("Article 2")
    assert text[spans[2][0]:spans[2][1]].startswith("Rule 3-A.1")


def test_structural_boundaries_recognizes_section_symbol_marker():
    adapter = LegalAdapter()
    text = "§ 12 Some provision text.\n§ 13 Another provision.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 2
    assert text[spans[0][0]:spans[0][1]].startswith("§ 12")
    assert text[spans[1][0]:spans[1][1]].startswith("§ 13")


def test_structural_boundaries_empty_for_no_provisions():
    adapter = LegalAdapter()
    assert adapter.structural_boundaries("Just plain prose with no provision markers.") == []


def test_legal_adapter_declares_references_edge_type():
    assert LegalAdapter.edge_types == frozenset({"references"})


def test_legal_adapter_name():
    assert LegalAdapter().name == "legal"


def test_llm_boundary_prompt_is_none():
    assert LegalAdapter().llm_boundary_prompt() is None


def test_registering_legal_adapter_does_not_change_core_vocabulary():
    register_adapter(LegalAdapter())
    assert all_edge_types() == {
        "references", "depends_on", "related_to",
        "implements", "contains", "evolves_into", "generated_from",
    }


def test_get_adapter_finds_explicitly_registered_legal_adapter():
    register_adapter(LegalAdapter())
    adapter = get_adapter("legal")
    assert adapter is not None
    assert adapter.name == "legal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_legal_adapter.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.adapters.legal'`

- [ ] **Step 3: Implement the skeleton**

Create `smos/ingest/adapters/legal.py`:

```python
from __future__ import annotations

import re
from typing import Optional

_PROVISION_RE = re.compile(
    r"(?:Section \d+(?:\.\d+)*|Article \d+|Rule \d+(?:-[A-Z])?(?:\.\d+)*|§\s*\d+)"
)


class LegalAdapter:
    """Domain adapter for legal/regulatory prose (Phase 5.5b slice 1).

    Structural grammar: split on top-level provision markers (Section N,
    Article N, Rule N, § N). Sub-clause lettering ((a)(1)(i)) is not
    separately spanned — it falls through to the segmenter's semantic/
    size-fit layers, the same granularity choice MarkdownAdapter makes for
    nested list items under a heading.

    Extraction rules: citation cross-references between provision markers,
    plus heuristic defined-term matching (a "X means Y" defining sentence,
    and any later occurrence of the same Title-Case phrase) — mirrors
    MarkdownAdapter's heading self-topic trick, generalized to legal
    defined terms instead of headings. No LLM boundary prompt yet.
    """

    name = "legal"
    edge_types: frozenset[str] = frozenset({"references"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        matches = list(_PROVISION_RE.finditer(text))
        if not matches:
            return []
        spans = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            spans.append((start, end))
        return spans

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_legal_adapter.py -v --tb=short`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all previously-passing tests still PASS (new file, new adapter, nothing else touched).

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/adapters/legal.py tests/test_legal_adapter.py
git commit -m "feat: add LegalAdapter skeleton with provision-marker structural grammar"
```

---

### Task 2: `LegalAdapter.extract_references` — citation cross-references + defined-term matching

**Files:**
- Modify: `smos/ingest/adapters/legal.py` (add regex constants, replace `extract_references` body)
- Test: `tests/test_legal_adapter.py` (append)

**Interfaces:**
- Consumes: `_PROVISION_RE` (Task 1, same file).
- Produces: `LegalAdapter.extract_references(text: str) -> list[tuple[str, str, str]]` real implementation. Consumed by Task 3 (e2e graph-rescue test) and Task 4 (bench script).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_legal_adapter.py`:

```python
def test_extract_references_returns_empty_without_matches():
    adapter = LegalAdapter()
    assert adapter.extract_references("plain lowercase text with nothing capitalized.") == []


def test_extract_references_citation_cross_reference():
    adapter = LegalAdapter()
    text = "Section 3.1 permits disclosure. See also Section 5.2 for exceptions."
    triples = adapter.extract_references(text)
    assert ("Section 5.2", "Section 3.1", "references") in triples


def test_extract_references_defining_pattern_emits_self_topic():
    adapter = LegalAdapter()
    text = "Covered Institution means any entity that holds more than ten billion dollars in assets under management."
    triples = adapter.extract_references(text)
    assert ("Covered Institution", "Covered Institution", "references") in triples


def test_extract_references_defining_pattern_handles_quoted_term():
    adapter = LegalAdapter()
    text = '"Qualified Custodian" means a bank or trust company that meets the segregation-of-assets standard.'
    triples = adapter.extract_references(text)
    assert any(target == "Qualified Custodian" for _source, target, _edge in triples)


def test_extract_references_title_case_phrase_without_definition():
    adapter = LegalAdapter()
    text = "Annual stress-test results for a Covered Institution must be submitted no later than March 31."
    triples = adapter.extract_references(text)
    assert any(target == "Covered Institution" for _source, target, _edge in triples)


def test_extract_references_only_uses_references_edge_type():
    adapter = LegalAdapter()
    text = "Section 1 permits disclosure. Section 2 restricts it. Covered Institution means a large entity."
    triples = adapter.extract_references(text)
    assert all(edge_type == "references" for _s, _t, edge_type in triples)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_legal_adapter.py -k extract_references -v --tb=short`
Expected: FAIL — `test_extract_references_returns_empty_without_matches` passes trivially (Task 1's stub already returns `[]`), but `test_extract_references_citation_cross_reference`, `test_extract_references_defining_pattern_emits_self_topic`, `test_extract_references_defining_pattern_handles_quoted_term`, and `test_extract_references_title_case_phrase_without_definition` FAIL (stub returns `[]`, `in triples` / `any(...)` assertions fail).

- [ ] **Step 3: Implement `extract_references`**

In `smos/ingest/adapters/legal.py`, add two new regex constants right after `_PROVISION_RE`:

```python
_DEFINING_PATTERN_RE = re.compile(
    r'"?([A-Z][A-Za-z]+(?:\s+[A-Z][a-z]+){1,3})"?\s+(?:means|shall mean)\b'
)
_TITLE_CASE_PHRASE_RE = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b"
)
```

Replace the `extract_references` method body:

```python
    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        """(source_name, target_name, "references") triples. Two independent
        kinds, merged into one list — `smos/ingest/segmenter.py::_wire_references`
        wires every edge from the *current leaf* to `concept:{target_name}`
        and discards `source_name`, so only `target_name` drives graph
        connectivity; `source_name` exists for interface completeness and
        this adapter's own tests.

        a) Citation cross-references: reuses `_PROVISION_RE`. The *last*
        provision marker found in `text` is this window's own
        nearest/enclosing provision; every *other* marker match becomes a
        citation target, e.g. "as defined in Section 3.1" inside a window
        whose own nearest provision is "Section 5.2" emits
        ("Section 5.2", "Section 3.1", "references"). One-directional by
        design (matches the design spec's literal wording): a provision's
        own leaf does not get a self-topic triple from this rule alone, so
        citation cross-references alone do not guarantee bidirectional
        graph-rescue — that guarantee comes from (b) below.

        b) Defined-term matching (heuristic phrase matching, no
        corpus-wide term registry):
          - `_DEFINING_PATTERN_RE` matches a "X means"/"X shall mean"
            defining sentence and emits a self-topic triple (term, term) —
            mirrors MarkdownAdapter's heading self-topic trick.
          - `_TITLE_CASE_PHRASE_RE` matches any 2-4-word Title-Case phrase
            (accepted false-positive risk: also fires on proper nouns and
            sentence-initial capitals — additive graph noise, not a
            correctness bug, per the design spec) and emits
            (nearest_provision, phrase, "references") for each occurrence.
            A segment that merely *uses* a term (without defining it)
            still resolves to the same concept:{name} node the defining
            segment created, purely because both triples' target lowercases
            to the same string. When no provision marker is present in the
            window, `nearest_provision` falls back to the phrase itself —
            harmless since source_name is unused by the wiring step.

        Deduplicates by target only (case-insensitive) — mirrors
        MarkdownAdapter's dedup, since only the target drives a new
        concept-node edge.
        """
        triples: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        def _add(source: str, target: str) -> None:
            target = target.strip()
            key = target.lower()
            if not target or key in seen:
                return
            seen.add(key)
            triples.append((source, target, "references"))

        provision_matches = list(_PROVISION_RE.finditer(text))
        nearest_provision: Optional[str] = (
            provision_matches[-1].group(0) if provision_matches else None
        )
        for m in provision_matches[:-1]:
            _add(nearest_provision, m.group(0))

        for m in _DEFINING_PATTERN_RE.finditer(text):
            term = m.group(1).strip()
            _add(term, term)

        for m in _TITLE_CASE_PHRASE_RE.finditer(text):
            phrase = m.group(0).strip()
            _add(nearest_provision if nearest_provision else phrase, phrase)

        return triples
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_legal_adapter.py -v --tb=short`
Expected: PASS (14 tests total)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/adapters/legal.py tests/test_legal_adapter.py
git commit -m "feat: implement LegalAdapter.extract_references (citations + defined-term matching)"
```

---

### Task 3: End-to-end graph-rescue proof via `do_ingest_document_schema`

**Files:**
- Test: `tests/test_legal_adapter.py` (append) — or a new file `tests/test_legal_adapter_e2e.py` if you prefer to keep unit and e2e tests separate; this plan appends to the same file for consistency with `test_markdown_adapter.py`'s single-file convention.

**Interfaces:**
- Consumes: `LegalAdapter` (Tasks 1-2); `markdown_to_payload` (existing, `smos/ingest/transformers/markdown_transformer.py`); `do_ingest_document_schema` (existing, `smos/tools/ingest_tools.py`); `build_compressed_context` (existing, `smos/compression/context_builder.py`, unmodified).
- Produces: nothing new — this task adds no production code; it proves Tasks 1-2 compose correctly against a real ingested document, mirroring `tests/test_reference_and_domain_tagging_e2e.py`'s markdown proof exactly.

- [ ] **Step 1: Write the test**

Append to `tests/test_legal_adapter.py`:

```python
from pathlib import Path

from smos.compression.context_builder import build_compressed_context
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_legal_cross_reference_graph_rescue_end_to_end(store: VectorStore) -> None:
    text = (
        "Section 1. Definitions.\n"
        "Covered Institution means any entity that holds more than ten billion "
        "dollars in assets under management.\n\n"
        "Section 2. Stress Testing.\n"
        "Annual stress-test results for a Covered Institution must be submitted "
        "no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="legal-corpus", native_id="doc-legal-e2e")

    result = do_ingest_document_schema(payload, store, adapter=LegalAdapter())
    assert result["status"] == "success"
    assert result["reference_count"] >= 1

    target_id = next(
        sid for sid in result["segment_ids"]
        if "Annual stress-test results" in store.get_by_uuid(sid)["content"]
    )

    context = build_compressed_context(
        "asset threshold definition ten billion dollars", k=1, store=store
    )
    assert target_id in context.sources
```

Note the `Path`, `build_compressed_context`, `markdown_to_payload`, `VectorStore`, `do_ingest_document_schema` imports go at the top of `tests/test_legal_adapter.py` alongside the existing `LegalAdapter`/registry imports from Task 1 — consolidate all imports at the top of the file rather than leaving them mid-file as shown above (shown split here only for readability of what's new).

- [ ] **Step 2: Run it**

Run: `pytest tests/test_legal_adapter.py -v --tb=short`
Expected: PASS (15 tests total) — the two provision markers ("Section 1", "Section 2") force `structural_boundaries` to split the text into two leaves, each short enough to fit as its own segment. The definition leaf's `extract_references()` call emits a self-topic triple targeting "Covered Institution"; the stress-test leaf's lookback window (300 chars, `_REFERENCE_LOOKBACK_CHARS`) reaches back far enough to include the definition sentence too, so its own extraction also targets "Covered Institution" via the Title-Case-phrase rule. Both leaves end up wired to the same `concept:covered institution` node, so a query that vector/BM25-matches the definition leaf reaches the stress-test leaf via `context_builder.py`'s existing 2-hop `edge_types=["references"]` expansion.

- [ ] **Step 3: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_legal_adapter.py
git commit -m "test: prove legal cross-reference graph-rescue end-to-end via do_ingest_document_schema"
```

---

### Task 4: New comparison arm in `tests/bench_legal_regulations.py`

**Files:**
- Modify: `tests/bench_legal_regulations.py` (add imports after line 50; add two new functions after `_cross_ref_comparison`, currently ending at line 259, before `def main():` at line 262; modify `main()`, currently lines 262-280)

**Interfaces:**
- Consumes: `LegalAdapter` (Tasks 1-2); `markdown_to_payload` (existing); `do_ingest_document_schema` (existing); `_CROSS_REF_SCENARIOS`, `_NOISE_DOCS`, `_fmt` (existing, same file); `build_compressed_context` (existing, same file, already imported).
- Produces: nothing consumed elsewhere — this is the final task, a standalone script demonstrating the fix, not pytest-collected.

- [ ] **Step 1: Add imports**

In `tests/bench_legal_regulations.py`, add three new imports right after the existing `from smos.memory.vector_store import VectorStore` line (currently line 50):

```python
from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.tools.ingest_tools import do_ingest_document_schema
```

- [ ] **Step 2: Add the new comparison functions**

In `tests/bench_legal_regulations.py`, insert these two functions right after `_cross_ref_comparison` (currently ending at line 259, right before `def main():` at line 262):

```python
def _run_adapter_scenario(scenario: dict, tmp_root: Path, graph_enabled: bool) -> bool:
    suffix = f"adapter_{scenario['name']}_{graph_enabled}"
    store = VectorStore(persist_path=tmp_root / f"adapter_{abs(hash(suffix))}")
    if not graph_enabled:
        store.graph.traverse = lambda *a, **k: []

    target_text = _fmt(scenario["target"], scenario["term"], backtick=False)
    anchor_text = _fmt(scenario["anchor"], scenario["term"], backtick=False)

    target_payload = markdown_to_payload(target_text, system="legal-bench", native_id=f"target-{suffix}")
    target_result = do_ingest_document_schema(target_payload, store, adapter=LegalAdapter())
    target_id = target_result["segment_ids"][0]

    anchor_payload = markdown_to_payload(anchor_text, system="legal-bench", native_id=f"anchor-{suffix}")
    do_ingest_document_schema(anchor_payload, store, adapter=LegalAdapter())

    for noise in _NOISE_DOCS:
        store.store(MemoryObject(type="doc", content=noise))

    result = build_compressed_context(scenario["query"], k=1, store=store)
    return target_id in result.sources


def _adapter_comparison(tmp_root: Path) -> tuple[float, float]:
    n = len(_CROSS_REF_SCENARIOS)
    print("=" * 72)
    print(f"3. CROSS-REFERENCE RECALL VIA LEGAL ADAPTER (schema-driven ingest, "
          f"plain prose, {n} scenarios, k=1)")
    print("=" * 72)
    print(f"{'Scenario':<42} {'Graph off':<12} {'Graph on'}")
    print("-" * 72)

    off_hits = 0
    on_hits = 0
    for scenario in _CROSS_REF_SCENARIOS:
        off_ok = _run_adapter_scenario(scenario, tmp_root, graph_enabled=False)
        on_ok = _run_adapter_scenario(scenario, tmp_root, graph_enabled=True)
        off_hits += off_ok
        on_hits += on_ok
        print(f"{scenario['name']:<42} {'FOUND' if off_ok else 'missed':<12} {'FOUND' if on_ok else 'missed'}")

    off_recall = off_hits / n
    on_recall = on_hits / n
    print("-" * 72)
    print(f"Graph off: {off_recall:.2f} ({off_hits}/{n})")
    print(f"Graph on:  {on_recall:.2f} ({on_hits}/{n})")
    print()
    return off_recall, on_recall
```

- [ ] **Step 3: Wire the new comparison into `main()`**

In `tests/bench_legal_regulations.py`, replace `main()` (currently lines 262-280):

```python
def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_legal_"))
    try:
        cite_pre, cite_now = _citation_comparison(tmp_root)
        plain_off, plain_on = _cross_ref_comparison(tmp_root, backtick=False)
        marked_off, marked_on = _cross_ref_comparison(tmp_root, backtick=True)

        print("=" * 72)
        print("SUMMARY")
        print("=" * 72)
        print(f"Citation/defined-term recall:              {cite_pre:.0%} -> {cite_now:.0%}")
        print(f"Cross-reference recall, plain prose:       {plain_off:.0%} -> {plain_on:.0%} (graph off -> on)")
        print(f"Cross-reference recall, backtick-marked:   {marked_off:.0%} -> {marked_on:.0%} (graph off -> on)")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
```

with:

```python
def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_legal_"))
    try:
        cite_pre, cite_now = _citation_comparison(tmp_root)
        plain_off, plain_on = _cross_ref_comparison(tmp_root, backtick=False)
        marked_off, marked_on = _cross_ref_comparison(tmp_root, backtick=True)
        adapter_off, adapter_on = _adapter_comparison(tmp_root)

        print("=" * 72)
        print("SUMMARY")
        print("=" * 72)
        print(f"Citation/defined-term recall:              {cite_pre:.0%} -> {cite_now:.0%}")
        print(f"Cross-reference recall, plain prose:       {plain_off:.0%} -> {plain_on:.0%} (graph off -> on)")
        print(f"Cross-reference recall, backtick-marked:   {marked_off:.0%} -> {marked_on:.0%} (graph off -> on)")
        print(f"Cross-reference recall, legal adapter:      {adapter_off:.0%} -> {adapter_on:.0%} (graph off -> on)")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
```

- [ ] **Step 4: Run it**

Run: `python tests/bench_legal_regulations.py`
Expected: script completes without error. The new "3. CROSS-REFERENCE RECALL VIA LEGAL ADAPTER" section should show **"Graph off: 0.00 (0/3)"** and **"Graph on: 1.00 (3/3)"** — the legal-adapter path moving plain-prose cross-reference recall from 0% to 100%, matching dimension 2's existing backtick-marked-terms result but *without* requiring the backtick convention. The pre-existing "Cross-reference recall, plain prose" line (dimension 2, `backtick=False`) should be unchanged at its prior value (still 0% "graph off"/"graph on" for the un-adapted raw-`store()` path — that code path is untouched by this task, proving the new adapter path is what closes the gap, not a change to shared machinery).

- [ ] **Step 5: Run the full pytest suite to confirm no regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS — `bench_legal_regulations.py` is not pytest-collected, so this is purely a check that nothing else broke.

- [ ] **Step 6: Commit**

```bash
git add tests/bench_legal_regulations.py
git commit -m "test: add legal-adapter comparison arm to bench_legal_regulations.py"
```

---

## Post-plan: HANDOFF update

Not a task with tests, but required before ending the session: update `docs/HANDOFF.md`'s "Where things stand" section to record Phase 5.5b slice 1 completion (commit range, test count delta, `bench_legal_regulations.py`'s new dimension-3 numbers, any deviations from this plan discovered during implementation), mark 5.5b **DONE** in the sub-phase table, and point at the next chosen track (5.5c, or Phase 4/Track B, per the Governor's call).
