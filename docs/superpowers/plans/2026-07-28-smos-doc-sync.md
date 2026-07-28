# SMOS Doc Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give this project's own documents (HANDOFF, README, ARCHITECTURE, CLAUDE.md, runbooks, specs, plans) real list/table-typed ingestion into SMOS's schema-driven pipeline, plus a repeatable, change-detected skill that keeps them synced.

**Architecture:** Two pieces. (1) `smos/ingest/transformers/markdown_transformer.py` gains a body-splitting step that classifies markdown lines into `list`/`table`/`paragraph` runs instead of lumping everything into one `paragraph` block per heading span — a pure superset of existing `ContentBlock` schema support, no schema version bump. (2) A new project skill, `.claude/skills/smos-doc-sync/`, whose `scripts/sync_docs.py` walks a curated document list, skips docs whose content hasn't changed since last ingest (tracked in a JSON manifest), and calls `do_ingest_document_schema()` directly against this project's `VectorStore` — no MCP server dependency.

**Tech Stack:** Python 3.10+, pydantic (existing `IngestionPayload`/`ContentBlock` models), pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-smos-doc-sync-design.md`

## Global Constraints

- Tests: `pytest tests/ -v --tb=short`. If it reports "No tests collected" unexpectedly, retry with `rtk proxy pytest tests/ -v --tb=short` before assuming something is broken (`docs/runbooks/environment.md`).
- Never invoke `python3` on this machine — use `python`/`pytest` only; `python3` resolves to a non-functional Windows Store stub (`docs/runbooks/environment.md`).
- No `ContentBlock`/`IngestionPayload` schema changes. `ContentBlock.block_type` is already `Literal["heading", "paragraph", "list", "table"]` in `smos/ingest/contract.py` — this work only makes the transformer emit cases the schema already allowed. Do not add a `schema_version` bump or touch `smos/ingest/contract.py`.
- `sync_docs.py` must call `do_ingest_document_schema()` directly against a `VectorStore` instance, never through the MCP layer — no `smos` MCP server is registered on this machine yet (`docs/runbooks/environment.md`).
- Code files (`.py`, `.js`, etc.) are out of scope for both tasks below — this plan is documents only. Code already has a working path: `tool_recursive_semantic_ingest` with `include_patterns` restricted to code extensions (a call-site convention, not something either task touches).
- Use forward slashes / `Path.as_posix()` for any stored `native_id` — this repo is developed on Windows but `native_id` values must be stable across platforms.

---

### Task 1: Type list/table blocks in the markdown transformer

**Files:**
- Modify: `smos/ingest/transformers/markdown_transformer.py` (full rewrite, ~90 lines)
- Test: `tests/test_markdown_transformer.py` (append new tests; do not remove existing ones)

**Interfaces:**
- Consumes: `ContentBlock`, `ExternalHandle`, `IngestionPayload` from `smos.ingest.contract` (unchanged, already imported).
- Produces: `markdown_to_payload(text: str, system: str, native_id: str, doc_name: Optional[str] = None) -> IngestionPayload` — **signature unchanged**, only its output's `block_type` values change for inputs containing markdown lists/tables. Task 2 imports this function by this exact name and signature.

- [ ] **Step 1: Write the failing tests**

Append these functions to `tests/test_markdown_transformer.py` (the file already imports `blocks_to_full_text`, `markdown_to_payload`, `VectorStore`, `do_ingest_document_schema`, and already has the `store` fixture — do not duplicate those):

```python
def test_markdown_to_payload_types_bullet_list_block():
    md = "# Title\n- item one\n- item two\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-list")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "list"]
    assert payload.blocks[1].text == "\n- item one\n- item two\n"


def test_markdown_to_payload_types_table_block():
    md = "# Title\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-table")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "table"]
    assert payload.blocks[1].text == "\n| a | b |\n|---|---|\n| 1 | 2 |\n"


def test_markdown_to_payload_multiline_list_item_stays_one_list_block():
    md = (
        "# Title\n"
        "- First item starts here\n"
        "  and wraps onto a continuation line.\n"
        "- Second item.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-wrap")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "list"]


def test_markdown_to_payload_mixed_list_table_paragraph_round_trips_byte_exact():
    md = (
        "# Title\n"
        "Intro paragraph.\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "\n"
        "Closing paragraph.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-mixed")
    full_text, _ = blocks_to_full_text(payload.blocks)
    assert full_text == md
    assert all(b.text.strip() for b in payload.blocks)


def test_end_to_end_ingest_with_lists_and_tables_reconstructs_original_markdown(
    store: VectorStore,
) -> None:
    md = (
        "# Title\n"
        "Intro paragraph.\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-mixed-ingest")
    full_text, _ = blocks_to_full_text(payload.blocks)

    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success"

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    reconstructed = "".join(
        full_text[r["metadata"]["char_start"] : r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == md
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_markdown_transformer.py -v --tb=short`
Expected: the 5 new tests FAIL (block_type sequences come back as all-`paragraph` instead of including `list`/`table`); the 4 pre-existing tests in this file still PASS.

- [ ] **Step 3: Replace `smos/ingest/transformers/markdown_transformer.py` with this content**

```python
from __future__ import annotations

import re
from typing import Optional

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload

_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.*\|[ \t]*$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")


def _classify_line(line: str, current_run_type: Optional[str]) -> str:
    """Classify one line (newline included) for _split_body_blocks. A line
    indented under an in-progress "list" run is treated as a wrapped
    continuation of that list item rather than its own paragraph, so a
    multi-line bullet stays inside one "list" block."""
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return "blank"
    if _TABLE_ROW_RE.match(stripped):
        return "table"
    if _LIST_ITEM_RE.match(stripped):
        return "list"
    if current_run_type == "list" and stripped[:1] in (" ", "\t"):
        return "list"
    return "paragraph"


def _split_body_blocks(text: str) -> list[ContentBlock]:
    """Split a heading-free span into typed ContentBlocks: a contiguous run
    of markdown table rows (lines starting and ending with `|`) becomes one
    "table" block, a contiguous run of list items (`-`/`*`/`+`/`N.`/`N)`
    markers, plus their indented wrapped-continuation lines) becomes one
    "list" block, everything else stays "paragraph" — matching the prior
    single-paragraph behavior for spans with no lists or tables. Not a full
    markdown parser: code fences, blockquotes, and nested/mixed list styles
    all still fall through to "paragraph", same as before this function
    existed.

    Blank lines are folded into whichever run precedes them (never given
    their own block), and a span that opens with blank lines before any real
    content (e.g. the leftover newline from the preceding heading's own
    regex match) has that leading whitespace folded forward into the first
    real block. Both rules exist so blocks_to_full_text's byte-exact
    reconstruction never has to drop a whitespace-only block — validate_payload's
    "block text must not be blank" rule would otherwise quarantine a payload
    with a real, structurally correct list or table simply because a blank
    line landed in its own block."""
    if not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    runs: list[tuple[str, str]] = []

    for line in lines:
        current_type = runs[-1][0] if runs else None
        kind = _classify_line(line, current_type)
        if kind == "blank" and runs:
            prev_type, prev_text = runs[-1]
            runs[-1] = (prev_type, prev_text + line)
        elif runs and runs[-1][0] == kind:
            prev_type, prev_text = runs[-1]
            runs[-1] = (prev_type, prev_text + line)
        else:
            runs.append((kind if kind != "blank" else "paragraph", line))

    blocks: list[ContentBlock] = []
    carry = ""
    for block_type, run_text in runs:
        combined = carry + run_text
        if not combined.strip():
            carry = combined
            continue
        blocks.append(ContentBlock(block_type=block_type, text=combined))
        carry = ""

    return blocks


def markdown_to_payload(
    text: str, system: str, native_id: str, doc_name: Optional[str] = None
) -> IngestionPayload:
    """Parse ATX headings plus typed list/table/paragraph body blocks into
    ContentBlocks and wrap in a valid IngestionPayload. Reference
    implementation proving the client side of the ingestion contract — see
    _split_body_blocks for exactly which markdown shapes get their own
    block_type and which still fall back to "paragraph"."""
    matches = list(_HEADING_RE.finditer(text))
    blocks: list[ContentBlock] = []

    if not matches:
        blocks.extend(_split_body_blocks(text))
        return IngestionPayload(
            external_handle=ExternalHandle(system=system, native_id=native_id),
            doc_name=doc_name,
            blocks=blocks,
        )

    if matches[0].start() > 0:
        blocks.extend(_split_body_blocks(text[: matches[0].start()]))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        blocks.append(ContentBlock(block_type="heading", text=m.group(0), heading_level=level))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.extend(_split_body_blocks(text[body_start:body_end]))

    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=doc_name,
        blocks=blocks,
    )
```

- [ ] **Step 4: Run the full transformer test file**

Run: `pytest tests/test_markdown_transformer.py -v --tb=short`
Expected: all 9 tests PASS (4 pre-existing + 5 new).

- [ ] **Step 5: Run the full test suite to check for regressions elsewhere**

Run: `pytest tests/ -v --tb=short`
Expected: PASS count is the pre-existing total + 5 (no other file imports `_split_body_blocks`/`_classify_line` directly, but `tests/test_contract_ingest.py` and others exercise `do_ingest_document_schema` with hand-built payloads, not `markdown_to_payload`, so they're unaffected). If any test outside `test_markdown_transformer.py` fails, stop and investigate before continuing — do not proceed to Task 2 with a regression in place.

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/transformers/markdown_transformer.py tests/test_markdown_transformer.py
git commit -m "feat: type list/table blocks in markdown_to_payload

ContentBlock.block_type already allowed \"list\"/\"table\"; the transformer
just never emitted them, collapsing every list and table in a doc into one
opaque paragraph block. No schema change — pure superset of prior output."
```

---

### Task 2: `smos-doc-sync` project skill

**Files:**
- Create: `.claude/skills/smos-doc-sync/SKILL.md`
- Create: `.claude/skills/smos-doc-sync/scripts/sync_docs.py`
- Create: `tests/test_doc_sync.py`
- Modify: `CLAUDE.md` (repo root) — one added line

**Interfaces:**
- Consumes: `markdown_to_payload(text, system, native_id, doc_name=None) -> IngestionPayload` (Task 1, `smos.ingest.transformers.markdown_transformer`); `content_hash(full_text: str) -> str` and `IngestionPayload` (`smos.ingest.contract`); `do_ingest_document_schema(payload: IngestionPayload, store: VectorStore, adapter: Optional[DomainAdapter] = None) -> dict` with `result["status"]` in `{"success", "quarantined"}` (`smos.tools.ingest_tools`); `MarkdownAdapter()` (`smos.ingest.adapters.markdown`, no constructor args); `VectorStore(persist_path: Path)` (`smos.memory.vector_store`).
- Produces: `discover_docs(repo_root: Path) -> list[tuple[Path, str, list[str]]]`, `load_manifest(manifest_path: Path) -> dict[str, str]`, `save_manifest(manifest_path: Path, manifest: dict[str, str]) -> None`, `sync_docs(repo_root: Path, store: VectorStore, manifest_path: Path) -> dict` returning `{"ingested": list[str], "skipped": list[str], "quarantined": list[str]}` — these four names and signatures are what `tests/test_doc_sync.py` imports and calls.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doc_sync.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / ".claude" / "skills" / "smos-doc-sync" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import sync_docs  # noqa: E402  (sys.path insert above must run first)

from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "docs" / "runbooks").mkdir(parents=True)
    (root / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (root / "docs" / "superpowers" / "plans").mkdir(parents=True)

    (root / "docs" / "HANDOFF.md").write_text("# Handoff\nCurrent state.\n", encoding="utf-8")
    (root / "README.md").write_text("# Readme\nProject overview.\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text("# Architecture\nData flow.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Instructions\nHow to work here.\n", encoding="utf-8")
    (root / "docs" / "runbooks" / "environment.md").write_text(
        "# Environment\nPython notes.\n", encoding="utf-8"
    )
    (root / "docs" / "superpowers" / "specs" / "one-design.md").write_text(
        "# Spec One\nDesign details.\n", encoding="utf-8"
    )
    (root / "docs" / "superpowers" / "plans" / "one-plan.md").write_text(
        "# Plan One\nTask list.\n", encoding="utf-8"
    )
    return root


_ALL_NATIVE_IDS = {
    "docs/HANDOFF.md",
    "README.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "docs/runbooks/environment.md",
    "docs/superpowers/specs/one-design.md",
    "docs/superpowers/plans/one-plan.md",
}


def test_discover_docs_finds_every_curated_source(doc_tree: Path) -> None:
    found = sync_docs.discover_docs(doc_tree)
    native_ids = {p.relative_to(doc_tree).as_posix() for p, _, _ in found}
    assert native_ids == _ALL_NATIVE_IDS


def test_first_run_ingests_every_doc(doc_tree: Path, store: VectorStore, tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert set(summary["ingested"]) == _ALL_NATIVE_IDS
    assert summary["skipped"] == []
    assert summary["quarantined"] == []
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest.keys()) == _ALL_NATIVE_IDS


def test_second_run_with_no_changes_ingests_nothing(
    doc_tree: Path, store: VectorStore, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    sync_docs.sync_docs(doc_tree, store, manifest_path)

    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert summary["ingested"] == []
    assert set(summary["skipped"]) == _ALL_NATIVE_IDS


def test_changing_one_doc_reingests_only_that_doc(
    doc_tree: Path, store: VectorStore, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    sync_docs.sync_docs(doc_tree, store, manifest_path)

    (doc_tree / "docs" / "HANDOFF.md").write_text(
        "# Handoff\nUpdated state.\n", encoding="utf-8"
    )

    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert summary["ingested"] == ["docs/HANDOFF.md"]
    assert set(summary["skipped"]) == _ALL_NATIVE_IDS - {"docs/HANDOFF.md"}
```

- [ ] **Step 2: Run the new test file to verify it fails**

Run: `pytest tests/test_doc_sync.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'sync_docs'` (the script doesn't exist yet).

- [ ] **Step 3: Create `.claude/skills/smos-doc-sync/scripts/sync_docs.py`**

```python
"""Sync this project's own curated documents into SMOS via the schema-driven
ingestion path (IngestionPayload / do_ingest_document_schema), with
content-hash-based change detection so only docs that changed since the
last run are re-ingested. See ../SKILL.md for when to run this.

Usage: python sync_docs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.contract import content_hash
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

DOC_SOURCES: list[tuple[str, str, list[str]]] = [
    ("docs/HANDOFF.md", "handoff", ["smos-project", "handoff"]),
    ("README.md", "readme", ["smos-project", "readme"]),
    ("ARCHITECTURE.md", "architecture", ["smos-project", "architecture"]),
    ("CLAUDE.md", "instructions", ["smos-project", "instructions"]),
    ("docs/runbooks/*.md", "runbook", ["smos-project", "runbook"]),
    ("docs/superpowers/specs/**/*.md", "spec", ["smos-project", "spec"]),
    ("docs/superpowers/plans/**/*.md", "plan", ["smos-project", "plan"]),
]

MANIFEST_FILENAME = "doc_sync_manifest.json"


def discover_docs(repo_root: Path) -> list[tuple[Path, str, list[str]]]:
    """Expand DOC_SOURCES into (path, doc_type, domain_tags) triples for
    files that currently exist, deduplicated and sorted by resolved path for
    deterministic ordering."""
    seen: dict[Path, tuple[str, list[str]]] = {}
    for pattern, doc_type, tags in DOC_SOURCES:
        for path in repo_root.glob(pattern):
            if path.is_file():
                seen.setdefault(path.resolve(), (doc_type, tags))
    return [
        (path, doc_type, tags)
        for path, (doc_type, tags) in sorted(seen.items(), key=lambda kv: str(kv[0]))
    ]


def load_manifest(manifest_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_manifest(manifest_path: Path, manifest: dict[str, str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def sync_docs(repo_root: Path, store: VectorStore, manifest_path: Path) -> dict:
    """Ingest every doc in DOC_SOURCES whose content_hash differs from the
    manifest's last-recorded hash (or is missing from the manifest). Returns
    {"ingested": [...], "skipped": [...], "quarantined": [...]} (native_id
    strings). Persists the manifest with updated hashes for every doc that
    was successfully (re-)ingested."""
    manifest = load_manifest(manifest_path)
    docs = discover_docs(repo_root)
    adapter = MarkdownAdapter()

    ingested: list[str] = []
    skipped: list[str] = []
    quarantined: list[str] = []

    for path, doc_type, tags in docs:
        native_id = path.resolve().relative_to(repo_root.resolve()).as_posix()
        text = path.read_text(encoding="utf-8")
        text_hash = content_hash(text)

        if manifest.get(native_id) == text_hash:
            skipped.append(native_id)
            continue

        payload = markdown_to_payload(
            text, system="smos-project-docs", native_id=native_id, doc_name=path.name
        )
        payload.doc_type = doc_type
        payload.domain_tags = tags
        payload.metadata = {"repo_path": native_id}

        result = do_ingest_document_schema(payload, store, adapter=adapter)
        if result["status"] == "success":
            ingested.append(native_id)
            manifest[native_id] = text_hash
        else:
            quarantined.append(native_id)

    save_manifest(manifest_path, manifest)
    return {"ingested": ingested, "skipped": skipped, "quarantined": quarantined}


if __name__ == "__main__":
    store = VectorStore(persist_path=_REPO_ROOT / ".smos")
    manifest_path = _REPO_ROOT / ".smos" / MANIFEST_FILENAME
    summary = sync_docs(_REPO_ROOT, store, manifest_path)
    print(json.dumps(summary, indent=2))
```

- [ ] **Step 4: Run the test file again to verify it passes**

Run: `pytest tests/test_doc_sync.py -v --tb=short`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: PASS count is the Task 1 total + 4, zero failures.

- [ ] **Step 6: Create `.claude/skills/smos-doc-sync/SKILL.md`**

```markdown
---
name: smos-doc-sync
description: Sync this project's curated documents (HANDOFF, README, ARCHITECTURE, CLAUDE.md, runbooks, specs, plans) into SMOS via the schema-driven ingestion path. Use at the end of a session, after the handoff skill, to re-ingest any docs that changed this session.
---

# SMOS Doc Sync

Ingests this project's own prose documents into SMOS's schema-driven pipeline
(`IngestionPayload` / `do_ingest_document_schema`) so they're queryable via
`tool_semantic_query` / `tool_recall` in future sessions, instead of relying on
raw re-reads. Complements the existing code-ingestion path
(`tool_recursive_semantic_ingest`, restricted to code extensions via
`include_patterns` — see `docs/runbooks/`); this skill is documents only.

## When to use

Run at the end of a session, right after the `handoff` skill writes its
resume note — so `docs/HANDOFF.md`'s just-written update gets synced along
with anything else that changed this session.

## What it does

`scripts/sync_docs.py` walks a curated document list (`DOC_SOURCES` in the
script — not a blanket glob), hashes each doc's current content, and compares
against `.smos/doc_sync_manifest.json` (last-ingested hash per doc). Only
docs whose hash changed (or that have never been ingested) get re-ingested;
everything else is skipped. The very first run has no manifest, so every
curated doc is ingested.

## How to run it

```bash
python .claude/skills/smos-doc-sync/scripts/sync_docs.py
```

Prints a JSON summary: `{"ingested": [...], "skipped": [...], "quarantined": [...]}`.
A non-empty `quarantined` list means a doc failed `IngestionPayload` validation —
check `store.get_quarantine(key)` (see `smos/ingest/contract.py::quarantine_payload`)
for the recorded errors before assuming the sync succeeded.
```

- [ ] **Step 7: Wire it into `CLAUDE.md`'s session-continuity section**

In `CLAUDE.md` (repo root), find this exact text in the "Current state & handoff" section:

```
**Read `docs/HANDOFF.md` first, every session.** It holds the current phase, what
shipped last session, and next steps. **Update it at the end of every session**
(what changed, what's next) so a fresh session can orient from that file alone.
```

Replace it with:

```
**Read `docs/HANDOFF.md` first, every session.** It holds the current phase, what
shipped last session, and next steps. **Update it at the end of every session**
(what changed, what's next) so a fresh session can orient from that file alone.
**Then run the `smos-doc-sync` skill** (`.claude/skills/smos-doc-sync/`) to
re-ingest any changed docs — including this file — into SMOS before ending
the session.
```

- [ ] **Step 8: Verify the live script runs cleanly against the real repo**

Run: `python .claude/skills/smos-doc-sync/scripts/sync_docs.py`
Expected: exits 0, prints a JSON summary with `"quarantined": []`. This is the
first real run — expect every curated doc to appear in `"ingested"` (no
manifest exists yet for the real `.smos/doc_sync_manifest.json`). This is
also the "initial full-repo document ingestion" the spec deferred to a
post-plan operational step (Track 3's document half) — it happens here as a
natural side effect of proving the script works, not as a separate task.

- [ ] **Step 9: Commit**

```bash
git add .claude/skills/smos-doc-sync/SKILL.md .claude/skills/smos-doc-sync/scripts/sync_docs.py tests/test_doc_sync.py CLAUDE.md
git commit -m "feat: add smos-doc-sync skill for change-detected document ingestion

Curated doc list -> IngestionPayload via markdown_to_payload -> direct
do_ingest_document_schema() call, gated by a content-hash manifest so only
changed docs are re-ingested. Wired into CLAUDE.md's session-end step."
```

---

## Self-Review Notes

- **Spec coverage:** Transformer upgrade (spec section A) → Task 1. Skill,
  DOC_SOURCES table, manifest-based change detection, direct VectorStore
  call, CLAUDE.md wiring (spec section B) → Task 2. Code-ingestion
  clarification and out-of-scope items (spec sections C) are constraints
  above, not tasks — nothing to build for them. Testing requirements (spec
  section D) are covered by Task 1 Step 1 and Task 2 Step 1.
- **Not covered by this plan (by design, per spec section C):** Track 1
  (global MCP install), Track 2's `CLAUDE.md` policy-flip edit (the
  "validation only" → active-SMOS-policy section swap), and the code half
  of Track 3's initial full-repo ingestion. These are operational follow-ups
  to run right after this plan ships, not implementation tasks.
- **Type consistency checked:** `markdown_to_payload`'s signature is
  identical before and after Task 1, so Task 2's import and call sites don't
  need to match anything new. `do_ingest_document_schema(payload, store,
  adapter=None)`'s three-arg signature (verified against
  `smos/tools/ingest_tools.py:623`) matches how Task 2 calls it.
