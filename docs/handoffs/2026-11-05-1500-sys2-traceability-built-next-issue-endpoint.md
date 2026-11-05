# Handoff — SYS.2 traceability built, issue-submission endpoint next — 2026-11-05 1500

## State
Session 13 built the SMOS side of the tool-user pipeline's phase-2 ledger (SYS.2 verification requirements will reach SMOS through Codebeamer
and a future parser, never from the pipeline). Everything is committed and **pushed to `origin/main`** (`55c4d7a`, `11a039d`); **no release was
tagged** (the Governor said no release). Verified: full suite **1209 passed**. **Not verified:** nothing was run against a real server or the
Debian store (no end-to-end check of `verifies`, anchors or the stale marker over HTTP). No server or background process was started this session,
so none is running.

## Completed this session
- Edge type `verifies` (RequirementsAdapter, SYS.2 -> SYS.1 only); `/requirement-context` walks it both ways (`verifies` / `verified by`), not counted as upstream.
- `cites` edges may carry `metadata.anchors` (`clause`, `quote`, `block_ordinal`, `char_start`, `char_end`); `wire_source_links` stamps `target_hash` for `cites`
  when the target is already ingested; `/requirement-context` reports each anchor `current` / `stale` / `unverified`.
- Normative items carry `anchors`, `doc_tags`, `edition_status` (live from the target's `status:<x>` tag).
- P2-006: `external_handle.version` and `deep_link` stored on the document node and listed by `GET /documents`.
- SMOS-013: hits carry `rank` and `rank_score` (fused order); `score` stays cosine.
- Docs: parser guide, query guide, API.md, known-limits updated; `docs/HANDOFF.md` has a session 13 section.
- Tests: 11 in `tests/test_requirement_context.py`, `tests/test_external_handle_fields.py` (new); two pinned-shape tests updated on purpose.

## Next steps (in order)
1. **Issue-submission endpoint: brainstorm first** (superpowers:brainstorming), then design doc in `docs/superpowers/specs/`. The Governor's brief: a new API
   point where clients submit issues with a special **issue token** (a separate token kind); an admin page lists submitted issues, the admin approves
   them, and approved ones are uploaded to GitHub Issues after authentication. Open questions to ask: who the submitters are (the tool-user and parser
   projects?), issue fields (the ledger table: ID, found, area, impact, observation, action, status), where the GitHub credential lives and how it is
   entered (admin page, never in the repo), which repo, whether duplicates/edits/rejections are needed, rate limiting and size caps.
   Existing pieces to reuse: token + scope machinery (`smos/admin`, admin DB migrations, invitation links), the router-level route-classification guard
   (`tests/test_api_scopes.py` pins the write list; `test_every_route_in_schema_is_protected` for auth), admin pages and CSRF.
2. Governor, on Debian: the v0.5.0 rollout steps from `docs/handoffs/2026-11-04-1210-v0-5-0-published-scoped-retrieval.md` are still open.
3. Later: the confidence benchmarks (below), then decide the next release (this work is unreleased; version is still 0.5.0).

## Open items / blockers
- **Confidence field parked** until two benchmark runs (current corpus ~9,970 segments, and a larger one). Finding: `confidence` is `(1 + cosine) / 2`
  averaged over the returned hits (`smos/compression/context_builder.py:44`), so it has a compressed scale and no "nothing relevant" signal; keyword-only
  hits count as 0.5. Baseline from the Governor's three queries with the read-only token: on-topic 0.807, same query scoped to `norm:MBN-10567` 0.800,
  off-topic 0.662. No earlier values exist, so a "drop" is unproven. Record confidence in the benchmark runs.
- Edge uniqueness: `(source, target, edge_type)` is unique and a duplicate is silently ignored (`INSERT OR IGNORE`). Several clauses of one norm must go
  in one `cites` link as `anchors`. Documented in the parser guide.
- If the norm arrives after the citing item, its anchors stay `unverified` until the citing item is re-sent.
- Not changed, decided: `requirement` documents stay non-normative; SYS.2 items are never normative evidence.
- Pipeline-side ledger items (Codebeamer, parser, pipeline) are not SMOS work. P2-005/P2-013 are answered by this release; tell the tool-user project
  when it ships (the `verifies` edge, anchors, `level:SYS.1` / `level:SYS.2` free-form tags, no change to normative rules).
- Deferred minors from the last handoff still apply (`_BLOCK_KEYS`, hits of `recall exact` / `requirement-context` lacking `block_*`, double query embedding, deep paging graph extra).
- GitHub MCP server failed to connect this session (authorization header); `gh` via Bash works for GitHub.

## Key decisions & context
- Norm unit is the chapter document (`DOC:<id>/cNN`); the edge targets the document, the clause is found via anchors and `/nodes/{id}/text?include=blocks`. No node per clause.
- `verifies` only between requirements: `linked` returns the full text of the other item, so a norm chapter there would flood the answer. Norm evidence goes through `cites`.
- Edition status is read live from the target's tags, never frozen on the edge.
- `target_hash` is stamped by SMOS at link time (my call); a sender-supplied one is kept.
- Pipeline never writes SYS.2 to SMOS: CSV/PDF, human review, upload to Codebeamer, a future parser ingests (like SYS.1).
- Inline work on `main`, no reviewer subagent (standing preference). No benchmark or company data in the repo.

## Files that matter
- `smos/memory/requirement_context.py` — `LINK_EDGE_TYPES`, `_anchor_states`, `_doc_status`, normative items.
- `smos/ingest/link_wiring.py` — `target_hash` stamping; `smos/ingest/adapters/requirements.py` — edge vocabulary.
- `smos/compression/context_builder.py`, `smos/memory/schemas.py` — `rank`, `rank_score`; confidence at line 44.
- `docs/guide/parser-author-guide.md` — `verifies`, anchors, edge uniqueness; `docs/guide/query-guide.md` — new fields.
- `docs/HANDOFF.md` — current-state page (session 13 section at the top).
