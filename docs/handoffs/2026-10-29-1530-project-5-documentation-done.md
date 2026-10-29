# Handoff — Project 5 (documentation) done, Codex review package 6 ready — 2026-10-29 1530

## State

Projects 1-5 are implemented. Debian runs 0.3.2 (the drill overlay on the 0.3.1 image); the real repository's latest release is
`v0.3.1`. Project 5 changed no SMOS behavior and needs no release (nothing under `smos/`, `pyproject.toml`, `Dockerfile` or
`compose.yaml` changed). 776 tests pass. No assistant-side server or process is running.

## Completed this session (after the 1030 handoff)

- Brainstorm, design (`docs/superpowers/specs/2026-10-29-documentation-design.md`), plan, inline execution.
- `docs/guide/`: README, concepts, parser-author-guide, query-guide, agent-playbook, troubleshooting-and-reporting,
  known-limits-and-roadmap. `docs/guide-verification.md` holds the real-run record, the findings and the readability pass.
- `docs/examples/standards_pdf_parser.py` (+ `requirements.txt`, `README.md`): a tested standards-PDF parser (`pypdf`, example only).
- Tests: `tests/test_example_standards_pdf.py` (20), `tests/test_docs_guide.py` (4: links, anchors, endpoints exist, every endpoint documented).
- README and `docs/API.md` refreshed. Codex package: `docs/superpowers/review/2026-10-29-6-review-package.md`.

## Findings recorded as technical debt (documented, NOT fixed; all in `docs/guide/known-limits-and-roadmap.md`)

1. `POST /documents` runs without a domain adapter: no reference detection from text; legal adapter not registered.
2. "Normative" evidence is hard-wired to `doctype:legal|standard`.
3. `tags` on `/query` is a preference (falls back to an unfiltered search; BM25 never filtered) although the API text says "restrict".
4. A query always returns its nearest items; `mode: uncertain` only on an empty result.
5. Accepted-but-ignored contract fields (`deep_link`, `version`, `source_updated_at`, `source_anchor`, `schema_version`, `confidence`,
   `transformer_*`, `ingested_at`).
6. Blocks under 20 characters merge into the previous segment, so a bare short heading ends up glued to the previous clause;
   headings never create sections. By design, documented; the example emits one block per clause.
7. No endpoint to read quarantined payloads, to delete or to list documents.

## Next steps (in order)

1. Governor: give Codex `docs/superpowers/review/2026-10-29-6-review-package.md`; apply findings (new review file `2026-10-29-6-review.md`).
2. Governor on Debian (steps in `docs/guide-verification.md` section 4): persistence across `docker compose down` / `up -d`, and a
   read-only token refused with 403 on `POST /api/v1/documents`.
3. Governor: read the guide as a newcomer; the author's readability pass cannot replace that.
4. Decide which technical-debt items above become the next project (candidates: adapter selection over the API, real `tags` filter,
   store the ignored fields, a minimum score, keep short headings with their body). Each is a SMOS change and a release (0.3.3 or
   higher, never a different 0.3.2: Debian runs the drill's 0.3.2 overlay).
5. Housekeeping still on the Governor: delete `C:\Private\smos-update-drill`, unpublish the old PyPI project.
6. Later: tickets API + admin panel; serve the guide through the API; HNSW only on the triggers in `docs/hnsw-assessment.md`.

## Key decisions & context

- Example parser: `pypdf` only in `docs/examples/requirements.txt`; tests use `pytest.importorskip("pypdf")`.
- Ruling: the example emits one block per clause (heading line + first paragraph; a bare parent heading moves into the next block)
  because of finding 6; recorded in `docs/guide-verification.md`.
- Gotcha for this machine: long Python heredocs in Bash mangle `\n` escapes (they became real newlines in written files); use the
  Edit/Write tools for text containing backslashes and verify with `ast.parse`.
- The final whole-branch review was done inline by the author (the Governor's standing rule: no reviewer subagents); Codex package 6
  is the independent check.

## Files that matter

`docs/guide/README.md`, `docs/guide-verification.md`, `docs/examples/standards_pdf_parser.py`, `tests/test_docs_guide.py`,
`docs/superpowers/review/2026-10-29-6-review-package.md`, `docs/HANDOFF.md`.
