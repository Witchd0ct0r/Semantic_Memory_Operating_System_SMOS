# SMOS Live Dogfooding — Document Ingestion Skill: Design Spec

## Context

The Governor wants this project to start using SMOS as a *live* tool in its own
development, not just a product being built through careful, arm's-length
methodology. That request decomposes into five independent tracks:

| # | Track | Status |
|---|---|---|
| 1 | Global MCP install/activation (`smos setup`, user-scope) | Operational follow-up, not designed here |
| 2 | Flip this repo's `CLAUDE.md` file-reading policy from "validation only" to the standard active-SMOS policy | **Resolved** (below), executed as a follow-up edit |
| 3 | Initial full-repo ingestion (code + documents) | Operational follow-up; documents flow through Track 4's skill on its first run, code flows through the existing legacy ingester |
| 4 | New project skill: convert this project's own documents into SMOS's ingestion schema | **This spec** |
| 5 | Upstream any generalizable improvement into the `smos-mcp` package itself, backward-compatible | Folded into this spec — see "A. Transformer upgrade" below; turned out to need zero schema changes |

Tracks 1–3 are operational/policy actions, not design problems, and are executed
right after this spec lands. This document designs Track 4 (and the Track 5
transformer improvement it depends on).

## Resolved decisions

- **Policy flip (Track 2):** accept the known risk. The current "validation
  only, never general reading" policy in this repo's `CLAUDE.md` exists because
  of a real incident — `tool_read_file_compress` hallucinated a nonexistent
  `_init_db_and_index()` method reading this repo's own `vector_store.py` during
  Phase 1 (see cross-session memory `bm25-phase1-lessons`). The Governor's
  explicit call: flip it anyway, because surfacing exactly this kind of failure
  under real use is the point of a live test, not something to avoid.
- **Code stays on the existing legacy path.** `tool_recursive_semantic_ingest`
  already supports `include_patterns`/`exclude_patterns` (comma-separated
  `fnmatch` globs, matched against both filename and relative path —
  `smos/tools/ingest_tools.py:124-171`). Restricting it to code is a call-site
  parameter, not new functionality:
  `include_patterns="*.py,*.js,*.ts,*.tsx,*.jsx,*.java,*.go,*.rs,*.c,*.cpp,*.h,*.hpp"`.
  No schema is needed for code; the `IngestionPayload` schema (headings /
  paragraphs / lists / tables) doesn't model function/class structure and would
  be a downgrade for source files. This convention gets recorded in
  `docs/runbooks/` when Track 3 executes, so it isn't re-derived (or run
  wide-open by accident) later.
- **Documents go through the schema-driven path.** Prose docs (`HANDOFF.md`,
  `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, runbooks, specs, plans) are
  structure-preservable and go through `IngestionPayload` /
  `do_ingest_document_schema()`, via the new skill below. No file is ingested
  through both paths.

## A. Transformer upgrade — `smos/ingest/transformers/markdown_transformer.py`

**Problem:** `markdown_to_payload()` currently splits text only on ATX
headings; everything in a body span — including markdown lists and tables —
becomes a single opaque `paragraph` block. This repo's own docs are heavy on
both (HANDOFF's sub-phase table, README's benchmark tables, bullet-list
next-steps sections), so ingesting them as-is would defeat the point of the
structure-preserving schema.

**Fix:** extend the body-splitting step so each span between headings (and the
pre-heading lead, if any) is further split into contiguous runs of:
- **list** — contiguous lines matching a list-item marker (`-`, `*`, `+`, or
  `N.`/`N)`), including any wrapped continuation lines indented under an item
- **table** — contiguous pipe-delimited rows including the `---`-style header
  separator row
- **paragraph** — everything else (unchanged behavior)

Each run becomes a `ContentBlock` with `block_type` set accordingly and `text`
holding the raw markdown for that run verbatim (no reformatting — the block
stores exactly what was there, matching the byte-exact-reconstruction
guarantee `reconstruct()` already depends on elsewhere in the pipeline).

**Backward compatibility:** `ContentBlock.block_type` is already
`Literal["heading", "paragraph", "list", "table"]` in `smos/ingest/contract.py`
— this is a **schema-version-free** change. No `IngestionPayload.schema_version`
bump, no migration. Existing payloads/tests that only ever produced
heading/paragraph blocks are unaffected; this only adds cases the schema
already permitted but the reference transformer never emitted. This is the
"update the global tool, keep backward compatibility" ask (Track 5) — there
turns out to be no separate upstreaming step because the transformer already
lives in the product (`smos/ingest/transformers/`), not in project-only code.

## B. New project skill — `.claude/skills/smos-doc-sync/`

**`SKILL.md`:** states purpose (re-ingest this project's own curated documents
into SMOS via the schema-driven path) and trigger (run at session end, after
the `handoff` skill).

**`scripts/sync_docs.py`:**

- **`DOC_SOURCES`** — a curated table, not a blanket glob:

  | Source | `doc_type` | `domain_tags` |
  |---|---|---|
  | `docs/HANDOFF.md` | `handoff` | `["smos-project", "handoff"]` |
  | `README.md` | `readme` | `["smos-project", "readme"]` |
  | `ARCHITECTURE.md` | `architecture` | `["smos-project", "architecture"]` |
  | `CLAUDE.md` | `instructions` | `["smos-project", "instructions"]` |
  | `docs/runbooks/*.md` | `runbook` | `["smos-project", "runbook"]` |
  | `docs/superpowers/specs/**/*.md` | `spec` | `["smos-project", "spec"]` |
  | `docs/superpowers/plans/**/*.md` | `plan` | `["smos-project", "plan"]` |

- **Change detection:** a manifest at `.smos/doc_sync_manifest.json`, mapping
  each doc's `native_id` (repo-relative path, POSIX-style) to the
  `content_hash()` (already in `contract.py`) it had at last successful
  ingest. Each run: read current text, hash it, compare to the manifest entry.
  - No entry, or hash differs → doc is "changed", gets (re-)ingested.
  - Hash matches → skipped.
  - Missing/empty manifest (first run) → every doc counts as changed → full
    ingest of the curated set, matching the "first run ingests everything"
    requirement.
  - Manifest entry is updated only after a successful (non-quarantined) ingest.

- **Ingestion call:** for each changed doc, build the payload via the upgraded
  `markdown_to_payload(text, system="smos-project-docs", native_id=<repo-relative path>, doc_name=<filename>)`,
  set `doc_type`/`domain_tags` from `DOC_SOURCES`, `metadata={"repo_path": ...}`,
  then call `do_ingest_document_schema(payload, store)` **directly against
  this project's own `VectorStore`** (bound to this repo's `.smos/` persist
  path) — not through the MCP layer. This matches the documented no-MCP
  workaround (`docs/runbooks/environment.md`) and means the skill works
  whether or not Track 1's global MCP registration has happened yet.

- **Wiring:** one line added to this project's `CLAUDE.md`, in the session
  continuity section: run `smos-doc-sync` at session end, alongside/after the
  `handoff` skill.

## C. Explicitly out of scope

- Track 1 (global MCP install), Track 2's execution (the actual `CLAUDE.md`
  edit — the decision is resolved above, but flipping the file is a follow-up
  action, not part of this plan), Track 3 (the first real full-repo ingestion
  run, code + docs) — all operational steps taken right after this design is
  implemented, not planned in detail here.
- Doc deletion/rename handling in the manifest — not handled in v1. A renamed
  doc is ingested as a new `native_id` (new `doc_id`); the old `doc_id`'s
  segments are not cleaned up. Flagged as future work, not blocking for a
  small, stable curated doc set.
- Any change to `do_ingest_document_schema()`'s re-embed-on-change behavior
  (it fully re-processes a doc on any content change, no partial diff) — fine
  at this doc set's size, not something this spec touches.

## Testing

- **Transformer:** a fixture markdown string containing a heading, a bullet
  list, a table, and a plain paragraph; assert `markdown_to_payload()` emits
  blocks in the exact expected `block_type` sequence with byte-exact `text`.
- **`sync_docs.py`:** using a temp `.smos` store and a temp doc tree,
  1. first run with no manifest ingests every doc in `DOC_SOURCES`,
  2. a second run with no file changes ingests nothing (manifest hashes all
     match),
  3. modifying exactly one doc and re-running re-ingests only that doc and
     updates only its manifest entry.
