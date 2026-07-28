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
