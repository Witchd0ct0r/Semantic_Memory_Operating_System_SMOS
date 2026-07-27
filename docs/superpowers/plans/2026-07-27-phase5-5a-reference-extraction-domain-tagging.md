# Phase 5.5a — Reference Extraction & Domain Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two concrete, verified gaps blocking Pillar F (spec §8.5) from having any real data to work with: `MarkdownAdapter.extract_references()` is currently a no-op (the Phase 2 memory graph stays inert on every ingested document), and `IngestionPayload.domain_tags` is parsed but never persisted anywhere in `do_ingest_document_schema()`. This plan makes both real, wires reference extraction into both ingestion code paths (`ingest_document_text()` and `do_ingest_document_schema()`), and proves end-to-end that a plain markdown document with an internal cross-reference link gets graph-rescued at retrieval time — the same mechanism `tests/bench_legal_regulations.py` proved for backtick-marked code entities, now working on unmarked prose via markdown's native heading/link conventions.

**Architecture:** `MarkdownAdapter.extract_references(text)` finds the nearest heading in `text` (by regex) and any internal links/wiki-links within it, returning `(source_name, target_name, "references")` triples — including a "self-topic" triple `(heading, heading, "references")` so a defining section links to its own concept even without an outgoing link. A new `smos/ingest/segmenter.py` helper, `_wire_references()`, calls this per leaf and wires `segment --edge_type--> concept:{target_name}` edges — the exact shape `VectorStore._update_graph()` already uses for code entities (`memory --references--> entity:{name}`), so `context_builder.py`'s existing 2-hop `edge_types=["references"]` graph-expansion step picks it up with **zero retrieval-side changes**. Because the schema-driven ingestion path (`do_ingest_document_schema`) delivers headings and their body paragraphs as *separate* `ContentBlock`s (by the ingestion contract's own design — see spec §8.6), a per-leaf-only extraction would miss the heading entirely for body leaves; `_wire_references()` compensates with a fixed-size lookback window into `full_text` before each leaf, and `extract_references()` takes the *last* heading match in whatever text it's given (not the first), so the nearest preceding heading is always the one picked up regardless of which leaf boundary split it from its body. Both fixes were verified empirically against the real ingestion pipeline (not just reasoned about) before writing this plan — see the task steps for the exact scenarios.

**Tech Stack:** Python, SQLite (stdlib `sqlite3`), pytest, `re`. No new dependencies.

## Global Constraints

- **Scope fence: this is sub-phase 5.5a only.** Do not implement domain **classification** (adapter rules + LLM fallback), the fan-out evidence-gathering primitives (`resolve`/`traverse`/`classify_domains`/per-domain `recall`/`coverage_check`), the coverage/gap signal, or new SE-specific edge-type vocabulary (`refines`/`satisfies`/`verified_by`/etc.). Those are 5.5b+, deliberately deferred until there's a concrete non-markdown adapter to validate them against (Governor decision, 2026-07-27). This plan touches only: `MarkdownAdapter.extract_references()`, a new `_wire_references()` helper, its two call sites, and `domain_tags` persistence in `do_ingest_document_schema()`.
- **Do not modify `smos/compression/context_builder.py`.** Verified by direct testing (see Task 2) that its existing `store.graph.traverse(top[0]["id"], max_hops=2, edge_types=["references"])` graph-expansion step already surfaces the new concept-linked segments with no change — the whole point of reusing the `edge_type="references"` + `concept:{name}` shape `VectorStore._update_graph()` already established for code entities.
- **`source_name` is threaded through triples but not consumed by the wiring code in this plan.** `DomainAdapter.extract_references()`'s documented contract returns `(source_name, target_name, edge_type)`; `_wire_references()` only uses `target_name` and `edge_type` today. This is an intentional, documented simplification — not a bug — matching how `VectorStore._update_graph()` already wires code entities (`memory -> entity`, no "two concepts linked to each other" edge). Revisit only if a future adapter needs distinct source-vs-target concept semantics.
- **Edge-type validation is against the adapter's own declared `edge_types`, not the registry-wide `all_edge_types()`.** This directly enforces `DomainAdapter.extract_references()`'s documented invariant ("every edge_type returned must be a member of self.edge_types") rather than the looser global union — catches a misbehaving adapter emitting an edge_type it never declared, even if some other registered adapter happens to declare it.
- **Concept node identity is normalized to `target_name.strip().lower()`.** Anchor-style targets (`#stress-test` → "stress test") and heading-text targets ("Stress Test") must resolve to the same graph node for the graph-rescue mechanism to work across a document's own internal links. This is a known, accepted heuristic limitation: two unrelated same-named headings in the same corpus (e.g. two sections both titled "Overview") will share a concept node. Consistent with the existing entity-extraction heuristic's own imprecision (Phase 2); not a blocking issue for this slice.
- **`_REFERENCE_LOOKBACK_CHARS = 300`** in `smos/ingest/segmenter.py` — a fixed, generous, non-markdown-specific lookback window into `full_text` before each leaf, so the "nearest preceding heading" is available to the adapter's extraction call even when a client transformer splits a heading and its body into separate blocks (verified empirically in Task 3; see that task's steps for the exact before/after scenario).
- **Additive-only changes to result dicts.** `ingest_document_text()` and `do_ingest_document_schema()` both gain a new `"reference_count"` key; no existing key changes shape or is removed. Verified by inspection: no test in the current suite asserts an exact dict shape via `==` on either function's return value (all existing assertions check individual keys).
- **No new runtime dependencies.**
- **Test collection:** `pytest.ini` sets `python_files = ["test_*.py"]`. New tests go in `test_*.py` files under `tests/`.
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"**, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious (match the style already in every file this plan touches).

---

### Task 1: `MarkdownAdapter.extract_references()` — real link/wiki-link/self-topic extraction

**Files:**
- Modify: `smos/ingest/adapters/markdown.py` (whole file — currently 36 lines)
- Test: `tests/test_markdown_adapter.py` (modify one existing test, add five new ones)

**Interfaces:**
- Consumes: nothing new.
- Produces: `MarkdownAdapter.extract_references(text: str) -> list[tuple[str, str, str]]` (real implementation, replacing the no-op); `MarkdownAdapter.edge_types` changes from `frozenset()` to `frozenset({"references"})`. Consumed by Task 2's `_wire_references()`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_markdown_adapter.py`, replace the existing `test_extract_references_returns_empty_for_now` test:

```python
def test_extract_references_returns_empty_for_now():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("# Title\nSome text with `no refs`.") == []
```

with:

```python
def test_extract_references_emits_self_topic_triple_when_heading_present():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("# Title\nSome text with `no refs`.") == [
        ("Title", "Title", "references")
    ]


def test_extract_references_returns_empty_without_heading():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("Just plain text, no headings, no links.") == []


def test_extract_references_captures_anchor_link_target():
    adapter = MarkdownAdapter()
    text = "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be submitted.\n"
    triples = adapter.extract_references(text)
    assert ("Stress Test", "Stress Test", "references") in triples
    assert ("Stress Test", "definitions", "references") in triples


def test_extract_references_captures_wikilink_target():
    adapter = MarkdownAdapter()
    text = "## Section A\nSee [[Covered Institution]] for the definition.\n"
    triples = adapter.extract_references(text)
    assert ("Section A", "Covered Institution", "references") in triples


def test_extract_references_skips_external_urls():
    adapter = MarkdownAdapter()
    text = "## External\nSee [the site](https://example.com) for more.\n"
    assert adapter.extract_references(text) == [("External", "External", "references")]


def test_markdown_adapter_declares_references_edge_type():
    assert MarkdownAdapter.edge_types == frozenset({"references"})
```

Leave every other existing test in the file unchanged (`test_structural_boundaries_splits_on_headings`, `test_structural_boundaries_empty_for_no_headings`, `test_llm_boundary_prompt_is_none`, `test_registering_markdown_adapter_does_not_change_core_vocabulary`, `test_get_adapter_finds_explicitly_registered_markdown_adapter`) — `test_registering_markdown_adapter_does_not_change_core_vocabulary`'s exact-set assertion still holds after this task, since `"references"` is already in `_CORE_EDGE_TYPES` (`smos/ingest/adapters/registry.py`) and a union with itself doesn't change the result.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_markdown_adapter.py -v --tb=short`
Expected: the modified test fails (`[] != [("Title", "Title", "references")]`); the five new tests fail (`extract_references` currently always returns `[]`; `edge_types` is currently `frozenset()`).

- [ ] **Step 3: Implement the real extractor**

Replace the entire contents of `smos/ingest/adapters/markdown.py` with:

```python
from __future__ import annotations

import re
from typing import Optional

_HEADING_RE = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_EXTERNAL_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


class MarkdownAdapter:
    """Minimal domain adapter proving the DomainAdapter interface end-to-end.

    Structural grammar: split on markdown ATX headings (# through ######).
    Extraction rules: internal markdown links (`[text](#anchor)`,
    `[text](other-doc)`) and wiki-links (`[[Target]]`) — real cross-references
    a markdown corpus actually contains. External URLs are skipped (not
    references into this corpus). Every extraction call also emits a
    "self-topic" triple `(heading, heading, "references")` for the nearest
    heading in the given text, so a section that *defines* a term links to
    that term's concept node even when it contains no outgoing link itself —
    without this, a separate section that only *links to* the defining
    section's anchor would have nothing to connect to. No LLM boundary
    prompt yet.
    """

    name = "markdown"
    edge_types: frozenset[str] = frozenset({"references"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return []
        spans = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            spans.append((start, end))
        return spans

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        """(source_name, target_name, "references") triples. source_name is
        the *last* (nearest-to-the-end) heading found in `text` — callers
        that want "the heading nearest this specific span" pass a text
        window ending at that span (see `_wire_references` in
        `smos/ingest/segmenter.py`, which prepends a lookback window so a
        body block that arrived as a separate ContentBlock from its own
        heading still resolves the right source_name)."""
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return []
        source_name = matches[-1].group(0).lstrip("#").strip()
        if not source_name:
            return []

        triples: list[tuple[str, str, str]] = [(source_name, source_name, "references")]
        seen: set[str] = {source_name.lower()}

        for m in _LINK_RE.finditer(text):
            target = m.group(2).strip()
            if not target or _EXTERNAL_URL_RE.match(target):
                continue
            target_name = target[1:].replace("-", " ").strip() if target.startswith("#") else target
            if not target_name or target_name.lower() in seen:
                continue
            seen.add(target_name.lower())
            triples.append((source_name, target_name, "references"))

        for m in _WIKILINK_RE.finditer(text):
            target_name = m.group(1).strip()
            if not target_name or target_name.lower() in seen:
                continue
            seen.add(target_name.lower())
            triples.append((source_name, target_name, "references"))

        return triples

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_markdown_adapter.py -v --tb=short`
Expected: PASS (10 tests: 4 original unchanged + 1 modified + 5 new).

- [ ] **Step 5: Commit**

```bash
git add smos/ingest/adapters/markdown.py tests/test_markdown_adapter.py
git commit -m "feat: implement MarkdownAdapter.extract_references() for links, wiki-links, and self-topic triples"
```

---

### Task 2: `_wire_references()` in `segmenter.py`, wired into `ingest_document_text()`

**Files:**
- Modify: `smos/ingest/segmenter.py` (add `_wire_references` after `_wire_containment`, currently ending at line 203; modify `ingest_document_text`, currently lines 215-253)
- Test: `tests/test_segmenter_containment.py` (append)

**Interfaces:**
- Consumes: `DomainAdapter.extract_references` (Task 1), `GraphStore.add_node`/`.add_edge` (existing).
- Produces: `_wire_references(graph: GraphStore, adapter: DomainAdapter, full_text: str, leaves: list[Span]) -> int` (private helper); `ingest_document_text(...)`'s result dict gains a `"reference_count"` key. Consumed by Task 3 (imported into `ingest_tools.py`) and Task 5 (end-to-end test).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segmenter_containment.py`:

```python
def test_ingest_document_text_wires_reference_edges_for_shared_concepts(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten billion dollars.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be submitted by March 31.\n"
    )
    result = ingest_document_text("doc-refs", text, store, MarkdownAdapter())

    assert result["reference_count"] >= 1
    assert store.graph.get_node("concept:definitions") is not None

    defs_id = next(
        sid for sid in result["segment_ids"]
        if "Covered Institution means" in store.get_by_uuid(sid)["content"]
    )
    stress_id = next(
        sid for sid in result["segment_ids"]
        if "Annual results" in store.get_by_uuid(sid)["content"]
    )
    neighbors = store.graph.traverse(defs_id, max_hops=2, edge_types=["references"])
    assert any(n["id"] == stress_id and n["type"] == "memory" for n in neighbors)


def test_ingest_document_text_reference_count_is_zero_without_adapter_support(store: VectorStore) -> None:
    text = "A short document with no headings and no need to split further."
    result = ingest_document_text("doc-no-refs", text, store, _NoStructureAdapter())
    assert result["reference_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_segmenter_containment.py -v --tb=short`
Expected: FAIL — `KeyError: 'reference_count'` (key doesn't exist on the result dict yet).

- [ ] **Step 3: Implement `_wire_references` and wire it into `ingest_document_text`**

In `smos/ingest/segmenter.py`, add this new function immediately after `_wire_containment` (currently ends at line 203, `return section_count`), before `def build_span_tree` (currently line 206):

```python
_REFERENCE_LOOKBACK_CHARS = 300


def _wire_references(
    graph: GraphStore, adapter: DomainAdapter, full_text: str, leaves: list[Span]
) -> int:
    """Ask the adapter for cross-references near each leaf and wire them as
    `segment --edge_type--> concept:{target_name}` edges — the same shape
    `VectorStore._update_graph` already uses for code entities
    (`memory --references--> entity:{name}`). Two segments whose extracted
    triples share a target_name (normalized to lowercase) become connected
    through the shared concept node; `context_builder.py`'s existing 2-hop
    `edge_types=["references"]` graph-expansion step already walks exactly
    this shape, so no retrieval-side change is needed for it to take effect.

    Calls the adapter with a lookback window into `full_text` ending at each
    leaf, not just the leaf's own text — a client-supplied ingestion payload
    (Pillar G) commonly delivers a heading and its body as separate
    ContentBlocks, which become separate leaves with no heading text of
    their own; the lookback window recovers the nearest preceding heading
    regardless of which leaf boundary split it from its body.

    `source_name` is part of the DomainAdapter contract but unused here;
    edge_type must be one the adapter actually declares (`adapter.edge_types`)."""
    valid_edge_types = adapter.edge_types
    count = 0
    for leaf in leaves:
        if leaf.memory_id is None:
            continue
        window = full_text[max(0, leaf.start - _REFERENCE_LOOKBACK_CHARS):leaf.end]
        for _source_name, target_name, edge_type in adapter.extract_references(window):
            if edge_type not in valid_edge_types:
                continue
            concept_id = f"concept:{target_name.strip().lower()}"
            graph.add_node(concept_id, "concept", target_name.strip())
            graph.add_edge(leaf.memory_id, concept_id, edge_type)
            count += 1
    return count
```

Then modify `ingest_document_text`'s tail (currently the last 8 lines of the function):

```python
    section_count = _wire_containment(store.graph, doc_id, root)

    return {
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
    }
```

to:

```python
    section_count = _wire_containment(store.graph, doc_id, root)
    reference_count = _wire_references(store.graph, adapter, text, leaves)

    return {
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "reference_count": reference_count,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_segmenter_containment.py -v --tb=short`
Expected: PASS (5 tests: 3 original unchanged + 2 new).

This exact scenario (concept-sharing between a defining segment and a segment that links to it) was verified empirically against the real pipeline before this plan was written — `defs_id`'s neighbors at 2 hops via `edge_types=["references"]` include `stress_id` as a `"memory"`-type node, confirming the graph-rescue mechanism fires with no changes to `context_builder.py`.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all previously-passing tests still PASS (purely additive `reference_count` key; `_wire_references` is a new private helper, not a change to an existing signature).

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/segmenter.py tests/test_segmenter_containment.py
git commit -m "feat: wire extract_references() into ingest_document_text() via a lookback-windowed _wire_references helper"
```

---

### Task 3: Wire `_wire_references()` into `do_ingest_document_schema()`

**Files:**
- Modify: `smos/tools/ingest_tools.py:38` (import), `smos/tools/ingest_tools.py:718-735` (tail of `do_ingest_document_schema`)
- Test: `tests/test_contract_ingest.py` (append)

**Interfaces:**
- Consumes: `_wire_references` (Task 2, `smos/ingest/segmenter.py`).
- Produces: `do_ingest_document_schema(...)`'s result dict gains a `"reference_count"` key. Consumed by Task 5 (end-to-end test).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract_ingest.py` (add `from smos.ingest.adapters.markdown import MarkdownAdapter` to the existing import block at the top of the file):

```python
def test_schema_ingest_wires_reference_edges_when_adapter_supports_it(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten billion dollars.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be submitted by March 31.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-schema-refs")

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())

    assert result["status"] == "success"
    assert result["reference_count"] >= 1
    assert store.graph.get_node("concept:definitions") is not None


def test_schema_ingest_reference_count_is_zero_without_adapter(store: VectorStore) -> None:
    payload = _markdown_payload("# T\nBody text with no references.\n", "wiki", "doc-no-adapter")
    result = do_ingest_document_schema(payload, store)
    assert result["reference_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: FAIL — `KeyError: 'reference_count'`.

- [ ] **Step 3: Wire it in**

In `smos/tools/ingest_tools.py`, change the import (currently line 38):

```python
from smos.ingest.segmenter import _flatten_leaves, _wire_containment, build_span_tree
```

to:

```python
from smos.ingest.segmenter import _flatten_leaves, _wire_containment, _wire_references, build_span_tree
```

Then change `do_ingest_document_schema`'s tail (currently lines 718-735):

```python
    section_count = _wire_containment(store.graph, doc_id, root)
    store.save_checkpoint(
        doc_id=doc_id,
        content_hash=text_hash,
        total_leaves=len(leaves),
        next_leaf_index=len(leaves),
        segment_ids=segment_ids,
        status="complete",
    )

    return {
        "status": "success",
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "resumed": resumed,
    }
```

to:

```python
    section_count = _wire_containment(store.graph, doc_id, root)
    reference_count = _wire_references(store.graph, block_adapter, full_text, leaves)
    store.save_checkpoint(
        doc_id=doc_id,
        content_hash=text_hash,
        total_leaves=len(leaves),
        next_leaf_index=len(leaves),
        segment_ids=segment_ids,
        status="complete",
    )

    return {
        "status": "success",
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "reference_count": reference_count,
        "resumed": resumed,
    }
```

`block_adapter` (a `ContractBlockAdapter` wrapping `base_adapter`) is already in scope at this point in the function — its `.extract_references()` delegates directly to the base adapter's, and its `.edge_types` proxies the base adapter's declared set (both verified in `smos/ingest/contract.py`'s `ContractBlockAdapter`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: PASS (all tests, including the 2 new ones).

This scenario — headings and bodies arriving as separate `ContentBlock`s, which is exactly how `_markdown_payload`'s test helper and the production `markdown_to_payload` both build payloads — was verified empirically against the real ingestion pipeline before this plan was written; without the lookback window in Task 2's `_wire_references`, this test would fail (`reference_count == 0`) because the body leaf containing the link never sees its own heading text.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/tools/ingest_tools.py tests/test_contract_ingest.py
git commit -m "feat: wire reference extraction into do_ingest_document_schema"
```

---

### Task 4: Persist `domain_tags` in `do_ingest_document_schema()`

**Files:**
- Modify: `smos/tools/ingest_tools.py:682` (document node metadata), `smos/tools/ingest_tools.py:694-704` (segment `MemoryObject` construction)
- Test: `tests/test_contract_ingest.py` (append)

**Interfaces:**
- Consumes: `IngestionPayload.domain_tags` (existing field, currently unused — `smos/ingest/contract.py`).
- Produces: the `document` graph node's `metadata["domain_tags"]` now reflects `payload.domain_tags`; every segment's `MemoryObject.tags` now includes `payload.domain_tags`, making them reachable via `store.query_domain(..., domain_tags=[...])`. Consumed by Task 5 (end-to-end test).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract_ingest.py`:

```python
def test_domain_tags_persist_on_document_node_metadata(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-domains"),
        blocks=[ContentBlock(
            block_type="paragraph",
            text="Body text about lead and cadmium exposure limits for covered products.",
        )],
        domain_tags=["legal", "environmental"],
    )
    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node["metadata"]["domain_tags"] == ["legal", "environmental"]


def test_domain_tags_persist_on_segments_and_scope_query_domain(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-domains-2"),
        blocks=[ContentBlock(
            block_type="paragraph",
            text="Body text about lead and cadmium exposure limits for covered products.",
        )],
        domain_tags=["environmental"],
    )
    result = do_ingest_document_schema(payload, store)

    scoped = store.query_domain("lead cadmium exposure limits", k=5, domain_tags=["environmental"])
    assert any(r["id"] in result["segment_ids"] for r in scoped)

    unscoped = store.query_domain("lead cadmium exposure limits", k=5, domain_tags=["unrelated-domain"])
    assert not any(r["id"] in result["segment_ids"] for r in unscoped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: FAIL — `doc_node["metadata"]["domain_tags"]` is `[]` regardless of the payload's actual `domain_tags` (never written); the `query_domain` scoping assertion fails because segments carry no tags.

- [ ] **Step 3: Persist `domain_tags`**

In `smos/tools/ingest_tools.py`, change (currently line 682):

```python
    store.graph.add_node(doc_id, "document", payload.doc_name or doc_id)
```

to:

```python
    store.graph.add_node(
        doc_id, "document", payload.doc_name or doc_id,
        metadata={"domain_tags": payload.domain_tags},
    )
```

Then change the segment `MemoryObject` construction inside the chunk loop (currently lines 694-704):

```python
        memories = [
            MemoryObject(
                type="segment",
                content=full_text[leaf.start : leaf.end],
                doc_id=doc_id,
                ordinal=chunk_index_start + i,
                char_start=leaf.start,
                char_end=leaf.end,
            )
            for i, leaf in enumerate(chunk)
        ]
```

to:

```python
        memories = [
            MemoryObject(
                type="segment",
                content=full_text[leaf.start : leaf.end],
                doc_id=doc_id,
                ordinal=chunk_index_start + i,
                char_start=leaf.start,
                char_end=leaf.end,
                tags=list(payload.domain_tags),
            )
            for i, leaf in enumerate(chunk)
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: PASS (all tests, including the 2 new ones).

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/tools/ingest_tools.py tests/test_contract_ingest.py
git commit -m "fix: persist IngestionPayload.domain_tags onto the document node and its segments"
```

---

### Task 5: End-to-end proof — references and domain tags compose over a real ingest

**Files:**
- Test: `tests/test_reference_and_domain_tagging_e2e.py` (new)

**Interfaces:**
- Consumes: `markdown_to_payload` (Phase 5.3, `smos/ingest/transformers/markdown_transformer.py`); `MarkdownAdapter` (Task 1); `do_ingest_document_schema` (Tasks 3-4, `smos/tools/ingest_tools.py`); `build_compressed_context` (existing, `smos/compression/context_builder.py`).
- Produces: nothing new — this task adds no production code; it proves Tasks 1-4 compose correctly against a real ingested document, including through the production markdown-to-payload transformer (not just the tests' own `_markdown_payload` helper).

- [ ] **Step 1: Write the test**

Create `tests/test_reference_and_domain_tagging_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.compression.context_builder import build_compressed_context
from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_markdown_cross_reference_graph_rescue_end_to_end(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten "
        "billion dollars in assets under management.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be "
        "submitted no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="wiki", native_id="doc-e2e-refs")

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())
    assert result["status"] == "success"
    assert result["reference_count"] >= 1

    stress_id = next(
        sid for sid in result["segment_ids"]
        if "Annual results" in store.get_by_uuid(sid)["content"]
    )

    context = build_compressed_context(
        "asset threshold definition ten billion dollars", k=1, store=store
    )
    assert stress_id in context.sources


def test_domain_tags_and_references_compose_on_the_same_ingest(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten "
        "billion dollars in assets under management.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be "
        "submitted no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="wiki", native_id="doc-e2e-domains")
    payload.domain_tags = ["financial-regulation"]

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())

    assert result["status"] == "success"
    assert result["reference_count"] >= 1
    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node["metadata"]["domain_tags"] == ["financial-regulation"]

    scoped = store.query_domain(
        "stress test submission deadline", k=5, domain_tags=["financial-regulation"]
    )
    assert any(r["id"] in result["segment_ids"] for r in scoped)
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_reference_and_domain_tagging_e2e.py -v --tb=short`
Expected: PASS (2 tests) — no new production code in this task. Both scenarios were run against the real pipeline (via a temporary verification script, not committed) before this plan was written, confirming: the top-ranked result for a query matching the *Definitions* segment's content is that segment itself, and `context_builder.py`'s existing graph-expansion step pulls in the *Stress Test* segment as a second source purely through the shared `concept:definitions` node — with zero changes to `context_builder.py` itself.

- [ ] **Step 3: Run the full suite to confirm the whole sub-phase is green**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS — this is the final task in the plan.

- [ ] **Step 4: Commit**

```bash
git add tests/test_reference_and_domain_tagging_e2e.py
git commit -m "test: prove reference extraction and domain tagging compose end-to-end over a real ingest"
```

---

## Post-plan: HANDOFF update

Not a task with tests, but required before ending the session: update `docs/HANDOFF.md`'s "Where things stand" section to record Phase 5.5a completion (commit range, test count delta, any deviations from this plan discovered during implementation — in particular, note the lookback-window design if it needs tuning for a real corpus), and update the sub-phase table to mark 5.5a **DONE**, noting that domain classification, the fan-out primitives, and the coverage/gap signal remain as 5.5b+ (deliberately deferred, per the Governor's 2026-07-27 scoping decision) until there's a concrete non-markdown adapter to validate them against.
