# Project 5: Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline; the Governor chose inline execution). Steps use checkbox (`- [ ]`) syntax.

**Goal:** A guide in `docs/guide/` plus a runnable, tested standards-PDF example, so an agent or developer can write a parser, query SMOS and report problems using only a URL, a token and these docs.

**Architecture:** Facts first. A tested example script (`docs/examples/standards_pdf_parser.py`) forces the real contract behavior into the open; a probe run against a real local SMOS records the actual responses; the guide pages are then written from that record, never from memory. A mechanical docs test guards links and endpoint names.

**Tech Stack:** Markdown; Python 3.10+; `pypdf` (example only, in `docs/examples/requirements.txt`); stdlib `urllib` for the example's HTTP; pytest with the existing `api` fixture (`tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-10-29-documentation-design.md` (read it first; "Known-limits content" there lists behaviors the pages must state).

## Global Constraints

- SMOS behavior is not changed. No edits under `smos/`, `pyproject.toml`, `Dockerfile`, `compose.yaml` (rebuild-list files; no release is needed).
- English, plain Markdown, one purpose per page, short pages. No company, host or CA names; the example uses a made-up standard.
- `pypdf` lives only in `docs/examples/requirements.txt`. Tests that need it use `pytest.importorskip("pypdf")` so the suite stays hermetic.
- Every request/response shown in the guide was produced by a real run recorded in `docs/guide-verification.md`; trim, never invent.
- Test command: `rtk proxy python -m pytest tests -q`. Use `python`, not `python3`. Prefer the Write tool over long Bash heredocs.
- Facts already verified: payload cap 5,000,000 characters of block text; `POST /api/v1/documents` calls ingestion with no adapter; quarantine returns HTTP 200 `{"status":"quarantined","quarantine_key","errors"}`; schema violations 422; read-only token gets 403 on writes; `doc_id = sha1("system:native_id")[:16]`; blocks are concatenated with NO separator (`blocks_to_full_text`), so a parser puts its own newline at the end of each block's text.

## Review Focus

- A scanned PDF with no extractable text: the example exits with a clear message and posts nothing (not an empty-blocks quarantine).
- A heading with no body, and a document that starts with body text before any heading: both ingest successfully.
- Page headers/footers and bare page numbers repeated on every page are removed; legitimate repeated short clause text is not over-stripped (a line is furniture only if it appears on more than half the pages and there are at least 3 pages).
- Non-ASCII text (accents, en dashes, ligatures from the PDF) round-trips: `GET /nodes/{doc_id}/text` equals the concatenated block text exactly.
- A reference to the document's own standard, or the same reference many times, yields no self-link (rejected by the contract) and no duplicate links.
- Re-running the example on the same PDF is an idempotent upsert (same `doc_id`, no duplicate segments), and a changed PDF replaces the old content.

---

## File Structure

- Create `docs/examples/standards_pdf_parser.py` (library + CLI; one responsibility: PDF to contract payload and post it).
- Create `docs/examples/requirements.txt` (`pypdf`, pinned to the version tested).
- Create `docs/examples/README.md` (how to run it, 10 lines).
- Create `tests/test_example_standards_pdf.py`.
- Create `tests/test_docs_guide.py` (links, endpoint names).
- Create `docs/guide/{README,concepts,parser-author-guide,query-guide,agent-playbook,troubleshooting-and-reporting,known-limits-and-roadmap}.md`.
- Create `docs/guide-verification.md` (the run record, section 4 = Governor's Debian steps).
- Modify `README.md` (status paragraph, link to the guide), `docs/API.md` (title/version line, link to the guide), `docs/HANDOFF.md`.

---

### Task 1: The example parser (PDF to payload)

**Files:**
- Create: `docs/examples/standards_pdf_parser.py`, `docs/examples/requirements.txt`
- Test: `tests/test_example_standards_pdf.py`

**Interfaces:**
- Produces (module `standards_pdf_parser`, imported in tests via `importlib` from the file path):
  - `extract_pages(pdf_path: Path) -> list[list[str]]`: text lines per page (pypdf `extract_text().splitlines()`), raises `NoTextError(RuntimeError)` if every page is empty.
  - `strip_furniture(pages: list[list[str]]) -> list[str]`: flattened lines minus bare page numbers (`^\s*(page\s+)?\d+(\s+of\s+\d+)?\s*$`, case-insensitive) and minus any line that occurs on more than half the pages when there are at least 3 pages.
  - `to_blocks(lines: list[str]) -> list[dict]`: a line matching `^(\d+(?:\.\d+)*)\s+(\S.*)$` is a heading block (`block_type="heading"`, `heading_level=min(number of dot-separated parts, 6)`, `text=line + "\n"`); consecutive other non-blank lines join with a space into one paragraph block (`text=joined + "\n"`); blank lines end a paragraph. Every block text is non-empty and ends with `"\n"`.
  - `find_references(lines: list[str], own_designation: str) -> list[str]`: distinct designations matching `\b(?:ISO|IEC|EN|DIN)\s?\d{3,6}(?:-\d+)?\b`, whitespace-normalised to one space (`"ISO 26262-6"`), excluding `own_designation`, in first-seen order.
  - `build_payload(pdf_path: Path, system: str, designation: str, doc_name: str, doc_type: str = "standard", domain_tags: list[str] | None = None, summary: str | None = None) -> dict`: JSON-able dict with `external_handle={"system": system, "native_id": designation}`, `doc_name`, `blocks`, `doc_type`, `domain_tags`, `source_links=[{"target_native_id": ref, "edge_type": "references", "metadata": {"target_system": system}} for each reference]`, and `summary`/`summary_source="standards_pdf_parser 1"` only when a summary is given.
  - `post_document(base_url: str, token: str, payload: dict, opener=urllib.request.urlopen) -> dict`: POST JSON to `{base_url}/api/v1/documents` with the bearer header; returns the parsed JSON body; on `HTTPError` raises `SmosError(RuntimeError)` whose message holds the status and the response body.
  - `main(argv: list[str] | None = None) -> int`: CLI `--pdf --system --designation --name [--tag ...] [--summary ...] --url --token-env SMOS_TOKEN [--dry-run]`; `--dry-run` prints the payload summary (block count, link count) and posts nothing; prints `status`, `doc_id`, `segment_count`, or the quarantine errors; returns 0 on success, 2 on quarantine, 1 on any error.

- [ ] **Step 1: Write the failing tests** in `tests/test_example_standards_pdf.py`. A helper `make_pdf(pages: list[list[str]]) -> bytes` writes a minimal valid PDF by hand (one Helvetica font object, one content stream per page with `BT /F1 11 Tf 72 <y> Td (<escaped line>) Tj ... ET`, a correct xref table); module-level `pytest.importorskip("pypdf")`. Tests, named and with assertions:
  - `test_to_blocks_headings_and_paragraphs`: lines `["1 Scope","This part applies","to all items.","","1.1 Purpose","Text."]` give heading level 1, one joined paragraph `"This part applies to all items.\n"`, heading level 2, paragraph; every `text` ends with `"\n"`.
  - `test_strip_furniture_removes_repeating_header_and_page_numbers`: 4 pages each starting `"ACME STD 1000"` and ending `"Page N"` leave only body lines; with 2 pages nothing repeated is removed.
  - `test_find_references_dedupes_and_excludes_own`: text mentions `ISO 26262-6` twice, `iso26262-6` is not matched (case-sensitive), `ISO  9001`, and the own designation `ISO 12345`; result `["ISO 26262-6", "ISO 9001"]`.
  - `test_no_text_pdf_raises`: a PDF with empty pages raises `NoTextError`.
  - `test_build_payload_from_pdf`: round trip through `make_pdf`; `external_handle`, heading blocks, `source_links` shape, `doc_type == "standard"`.
  - `test_post_document_sends_bearer_json_and_surfaces_http_errors`: a fake `opener` records the request (URL, `Authorization`, JSON body) and returns a response object; a second fake raises `urllib.error.HTTPError(403, ...)` and the test expects `SmosError` mentioning `403`.
  - `test_dry_run_posts_nothing`: `main([... "--dry-run"])` with an opener that fails the test if called returns 0 (inject the opener through a module-level `OPENER` variable the CLI reads).
- [ ] **Step 2: Run** `rtk proxy python -m pytest tests/test_example_standards_pdf.py -q`. Expected: fails (module missing). If `pypdf` is not installed, `pip install -r docs/examples/requirements.txt` first.
- [ ] **Step 3: Implement** the interfaces above in `docs/examples/standards_pdf_parser.py` (stdlib plus `pypdf` imported inside `extract_pages`), and write `docs/examples/requirements.txt` with `pypdf==<installed version>`.
- [ ] **Step 4: Run** the test file. Expected: all pass.
- [ ] **Step 5: Mutation check**: temporarily break `strip_furniture` (return lines unchanged) and confirm its test fails; restore.
- [ ] **Step 6: Commit** (`docs(example): standards PDF parser with tests`).

### Task 2: The example against a real SMOS app (behavior facts)

**Files:**
- Modify: `tests/test_example_standards_pdf.py`

**Interfaces:**
- Consumes: Task 1 functions; fixture `api` from `tests/conftest.py` returning `(client, headers, app)`; a read-only token via `smos.admin.tokens.create_token(app.state.admin_db, "ro", scope="read")` (check the exact signature in `smos/admin/tokens.py`).

- [ ] **Step 1: Write failing tests** (each posts `build_payload(...)` output with `client.post("/api/v1/documents", json=payload, headers=headers)`):
  - `test_ingest_and_exact_reconstruction`: a 3-page generated PDF with accented text (`"Tűrés: ±0,5 mm – hőmérséklet"`); response `status == "success"`; `GET /api/v1/nodes/{doc_id}/text` equals `"".join(b["text"] for b in payload["blocks"])`.
  - `test_query_returns_the_matching_clause`: `POST /api/v1/query {"query": "<sentence from clause 2.1>", "k": 3}` returns a result whose `content` contains that sentence and whose `metadata["doc_id"]` equals the ingest `doc_id`.
  - `test_reingest_is_idempotent_and_changed_pdf_replaces`: same payload twice gives the same `doc_id` and the same `segment_count`; a payload with one changed clause leaves no trace of the old text in `/query`.
  - `test_read_only_token_cannot_ingest`: HTTP 403 with `{"detail": "this token is read-only"}`.
  - `test_link_to_an_uningested_standard`: payload with a `references` link to a designation that was never ingested; assert and record whatever really happens (success vs error). Write the assertion to match the observed behavior and add a one-line comment saying this pins documented behavior.
  - `test_heading_without_body_and_body_before_first_heading`: both ingest `"success"`.
  - `test_blocks_without_trailing_newline_run_together`: two blocks without `"\n"` reconstruct to text with no separator (documents why the example appends `"\n"`).
- [ ] **Step 2: Run**; expect failures only where an assumption is wrong. Where the real behavior differs from the plan's "Facts already verified", stop and note it in `docs/guide-verification.md` section 2 (the guide follows the real behavior).
- [ ] **Step 3: Fix the example** (not SMOS) until the tests pass.
- [ ] **Step 4: Full suite** `rtk proxy python -m pytest tests -q`; expected all pass.
- [ ] **Step 5: Commit** (`test(example): ingest, query, reconstruct, idempotency, scopes`).

### Task 3: The probe run on a real local server (verification record)

**Files:**
- Create: `docs/guide-verification.md`

**Interfaces:**
- Produces: a record the guide pages quote from. Sections: 1 how the run was set up; 2 real request/response pairs (trimmed) for: `GET /health`; token creation steps; ingest success; quarantine (a payload with an empty block); 422 (missing `blocks`); 401 (no token); 403 (read-only); `POST /query`; each `/recall` strategy (`hybrid`, `exact`, `temporal`, `chain`, `repo`); `POST /graph/query`; `POST /links`; `PUT /nodes/{id}/summary`; `GET /nodes/{id}/text`; `POST /requirement-context` with a standard ingested as `doc_type=standard` and a draft requirement; `POST/GET /verbatim`; `DELETE /memories/{id}`; 3 the `/query` parameter limits (`k` 1..100, `query` min length 10); 4 steps for the Governor on Debian (persistence across `docker compose down` then `up -d`, read-only token refused with 403 on `POST /api/v1/documents`; exact commands, expected output).

- [ ] **Step 1:** Start a real server on a temp data dir in the background (`SMOS_DATA_DIR=<scratchpad>/data SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=... SMOS_PORT=8799 smos-server`), create tokens the way the guide says (admin login, invitation, claim) using `curl`; if the invitation flow is awkward to script, create tokens with `smos.admin.tokens.create_token` against the same data dir and say so in the record.
- [ ] **Step 2:** Run the example with the generated PDF against it; run every request listed above with `curl`; paste real, trimmed output into the record. Note anything surprising (error texts, defaults, limits) under "Surprises".
- [ ] **Step 3:** Stop the server (check the port is free). Commit (`docs: guide verification record (real run)`).

### Task 4: Parser author guide, concepts and known limits

**Files:**
- Create: `docs/guide/concepts.md`, `docs/guide/parser-author-guide.md`, `docs/guide/known-limits-and-roadmap.md`

**Interfaces:**
- Consumes: Task 1-3 facts and `docs/guide-verification.md`.
- Produces: stable page names and headings other pages link to (`parser-author-guide.md#quarantine`, `#re-ingesting`, `#links`, `known-limits-and-roadmap.md#adapter-selection`).

- [ ] **Step 1:** `concepts.md`: what SMOS is and is not (never generates text; exact storage; hybrid retrieval), glossary (document, section, segment, `doc_id`, tags, summaries, edges, quarantine, token scopes), one diagram-in-text of document, sections, segments.
- [ ] **Step 2:** `parser-author-guide.md`: the contract field by field with the real limits; "blocks are concatenated: end each text with a newline" (with the recorded reconstruction result); how blocks become sections and segments (from the Task 2 run); `doc_type` and `domain_tags` and what `doctype:` does; `source_links` and the fact that SMOS does not detect references in text over the API; `#re-ingesting`; `#quarantine` (how to read it, how to fix and resend); size limit and what to do for bigger sources (split into documents); a pre-flight checklist; the worked PDF example (what it does, how to run it, its limits: heuristic headings, text PDFs only, not a general PDF parser).
- [ ] **Step 3:** `known-limits-and-roadmap.md`: each item from the spec's "Known-limits content" plus anything found in Task 3, each as: behavior today, why it matters to a parser or agent, status (`technical debt` or `future branch`), and the one-line proposed direction (adapter selection by `doc_type`/request field; configurable normative tags; link-vs-similarity separation in results; serving docs and a tickets API later).
- [ ] **Step 4:** Read the three pages as a reader with only these pages; fix gaps. Commit (`docs(guide): concepts, parser author guide, known limits`).

### Task 5: Query guide, agent playbook, troubleshooting and reporting

**Files:**
- Create: `docs/guide/query-guide.md`, `docs/guide/agent-playbook.md`, `docs/guide/troubleshooting-and-reporting.md`, `docs/guide/README.md`

**Interfaces:**
- Consumes: Task 3 record; links into Task 4 pages.

- [ ] **Step 1:** `query-guide.md`: a decision table (which endpoint for which need), every parameter with its limits (from `smos/api/models.py`), result fields (including `summary`, `section_summary`, `document_summary`, `score`, `metadata`), tag scoping, `confidence` and `mode`, recipes: "find the clause about X in a standard", "get the full text of a section", "follow references", "evidence for a requirement", with real recorded responses.
- [ ] **Step 2:** `agent-playbook.md` (one page): rules (read-only tokens for lookups, never invent text, quote `content` verbatim, cite `doc_id`/ids), the ingest flow, the query flow, what to do on 401/403/422/400/quarantine, when to stop and report.
- [ ] **Step 3:** `troubleshooting-and-reporting.md`: symptom to likely cause to action table built from the real errors recorded in Task 3; what an agent can check itself (`/health` version, a minimal reproducing request); the bug-report and feature-request templates (field list as in the spec); a "Future" section: the tickets API and admin panel (not built) and that these docs will be served via the API later.
- [ ] **Step 4:** `docs/guide/README.md`: index with one line per page and who should read it. Commit (`docs(guide): query guide, agent playbook, troubleshooting, index`).

### Task 6: Mechanical guard, README and API.md fixes

**Files:**
- Create: `tests/test_docs_guide.py`
- Modify: `README.md`, `docs/API.md`

**Interfaces:**
- Consumes: the `api` fixture's app (for `app.openapi()` or the served `/api/v1/openapi.json`).

- [ ] **Step 1: Write failing tests**:
  - `test_internal_links_resolve`: for every `.md` under `docs/guide/` and the README, each relative link target `[text](path#anchor)` exists, and a `#anchor` matches a heading slug in the target file.
  - `test_endpoints_named_in_the_guide_exist`: every `` `METHOD /api/v1/...` `` or `/api/v1/...` path in `docs/guide/*.md` matches an operation in the app's OpenAPI document (path templates compared with `{...}` placeholders normalised).
  - `test_guide_mentions_every_endpoint`: every operation in the OpenAPI document appears at least once in `docs/guide/` (so a new endpoint fails the test until documented).
- [ ] **Step 2:** Run; fix pages or tests until green. Mutation check: rename an endpoint in a guide page and confirm a test fails; restore.
- [ ] **Step 3:** `README.md`: replace the stale "SMOS 0.2 ... next projects" status paragraph with the real state (0.3.x: Docker, dashboard, self-update done) and add links to `docs/guide/README.md`. `docs/API.md`: title "SMOS API reference", a pointer to the guide, and the "Admin pages" and "Endpoints" sections left as they are.
- [ ] **Step 4:** Full suite green. Commit (`docs: guard tests, README and API.md refreshed`).

### Task 7: Readability pass, verification record, handoff, review package

**Files:**
- Modify: `docs/guide-verification.md`, `docs/HANDOFF.md`
- Create: `docs/handoffs/<date-time>-documentation-done.md`, `docs/superpowers/review/<date>-6-review-package.md`

- [ ] **Step 1: Readability pass:** open only `docs/guide/README.md` and follow it from a clean state: can the first parser be written, run and queried without opening any file outside the guide and the example? Record each stumble in `docs/guide-verification.md` section 5 and fix the page.
- [ ] **Step 2:** Run the example end to end once more against a fresh local server and confirm the guide's commands copy-paste correctly; stop the server.
- [ ] **Step 3:** Handoff: `docs/HANDOFF.md` (Project 5 done, what is left: the Governor's two Debian checks, Codex review) and a new handoff file; update the project memory.
- [ ] **Step 4:** Codex review package (format of the earlier ones in `docs/superpowers/review/`): scope is accuracy of the guide against the code, the example, and the guard tests; list what to attack (examples that no longer match, claims in the known-limits page, unreachable links, behaviors the guide hides).
- [ ] **Step 5:** Full suite; commit and push `main` (ask first if the push is blocked again).

---

## Self-review notes

- Spec coverage: goals 1-5 map to Tasks 4-6 (guide, example, verified examples, stale text, templates); verification items 1-6 map to Tasks 2, 3, 6, 7; the Governor's Debian steps are in the Task 3 record, section 4.
- Types: Task 2 and Task 6 consume only names defined in Task 1 and the existing fixtures.
- Proportion: code bodies are left to the implementer; the plan fixes names, regexes, limits and test assertions only.
