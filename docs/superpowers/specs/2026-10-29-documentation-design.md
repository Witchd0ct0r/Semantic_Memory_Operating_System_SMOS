# Project 5: documentation — design

Date: 2026-10-29. Status: awaiting the Governor's approval. Execution: inline (the Governor's standing choice).

## Intent and why

SMOS is installed and updatable, but only the person who built it can explain how to use it. The documentation lets two kinds of
reader work without that person:

- **Agents and human developers who write parsers.** The first one will read standards PDFs and fill SMOS; afterwards users query
  SMOS and get the relevant paragraphs and topics back.
- **Anyone who hits a bug or has an idea.** They should be able to diagnose a problem well enough to describe it clearly as a bug
  report or a feature request for SMOS.

Success: a developer or agent who has only a URL, a token and these docs can (a) understand the API, (b) write and run a working
parser, (c) query the result sensibly, and (d) when something fails, say what they did, what they expected, what happened and what
they already ruled out.

## Goals

1. A guide in `docs/guide/` (below), accurate to SMOS 0.3.x as it behaves today.
2. A runnable worked example: a small script that turns a standards PDF into the ingestion contract and posts it, with a test
   against a real local server.
3. Every example in the guide (requests and responses) is run against a real local SMOS before it is written down; the record of
   that run is kept.
4. The README and `docs/API.md` stop being stale (the README status paragraph, the "0.2" title) and link to the guide.
5. Bug-report and feature-request templates whose fields are chosen so a future ticket API can accept them unchanged.

## Non-goals

- No change to SMOS behavior. Where behavior is awkward, the docs describe it as it is and the gap goes into the known-limits
  document (technical debt), not into code.
- No ticket API or admin tickets panel. The docs carry a short future-reference note; they are updated when that project exists.
- Serving the docs through the API is future work; for now they are Markdown files in `docs/`, with a note saying so.
- No user guide for the admin dashboard beyond what `docs/INSTALL.md` already has.

## Anti-goals (would make this a failure even if every page exists)

- Examples that were never run, or that drift from the real responses.
- A parser guide that hides the behaviors that bite: no automatic reference detection over the API, the `doctype:` rule for
  "normative" evidence, quarantine returning HTTP 200, the 5,000,000-character payload cap.
- Pages written for the builder instead of the reader (internals, history, process talk).

## Constraints

- Plain Markdown in the repo, English, short pages, one purpose each; readable by an agent without rendering.
- No company, host or CA names anywhere in the repo or the example (memory: no company data in the repo). The example uses a
  made-up standard, not a real licensed one.
- The PDF library is used only by the example. It is not added to SMOS's runtime dependencies, its Docker image or
  `pyproject.toml` (my call: a new extra would change a rebuild-list file and force a "needs a rebuilt image" release for a
  docs-only change). It is listed in `docs/examples/requirements.txt` and installed with `pip install -r` by whoever runs the
  example. Pure-Python library preferred (the Governor chose "a PDF library given its size is small"; `pypdf` is the obvious
  candidate and is the builder's call to confirm).
- Tests that need the example run only when the library is installed (skip otherwise), so the SMOS suite stays hermetic and offline.

## Structure (the Governor's decisions, plus my marked guesses)

New folder `docs/guide/`:

| File | Content |
|---|---|
| `README.md` | Index; which page for which reader (agent, parser author, operator, reporter) |
| `concepts.md` | What SMOS is and is not; glossary: document, section, segment, `doc_id`, tags, summaries, edges, quarantine |
| `parser-author-guide.md` | Contract field by field; blocks to segments; `doc_type` and tags; `source_links`; idempotent re-ingest; quarantine; limits; checklist; the worked PDF example |
| `query-guide.md` | Which endpoint when; every parameter; result fields; tag scoping; recipes (including the standards-PDF case) |
| `agent-playbook.md` | One page of rules and flows for an agent |
| `troubleshooting-and-reporting.md` | Symptom to cause to action; bug-report and feature-request templates; future-reference note on tickets and on serving docs via the API |
| `known-limits-and-roadmap.md` | Behaviors that are limits today, each marked as technical debt or a future branch |

Plus `docs/examples/standards_pdf_parser.py` (the example) and its test.

My guess, not decided: the worked example's PDF heading detection is a simple heuristic (numbered clause headings such as
`5.2.1 Title`, page headers and footers stripped), good enough to show the contract, not a general PDF parser. The guide says so.

## Known-limits content the docs must state (verified in the code on 2026-10-29)

- `POST /documents` calls ingestion without a domain adapter, so SMOS does not detect cross-references in text; a parser declares
  links in `source_links`. The legal adapter is not registered. Recorded as technical debt, with a future branch "select an adapter
  by `doc_type` or a request field".
- "Normative" evidence in `/requirement-context` is hard-wired to items tagged `doctype:legal` or `doctype:standard` (set from
  `doc_type`); other scopes need `normative_tags`.
- Results are top-k only and do not separate "reached by explicit link" from "found by similarity only".
- A link whose target is the document itself is rejected; edge types are a fixed vocabulary plus adapter types.
- Whether a link may point at a document that is not ingested yet, and what happens when it is ingested later, must be tested
  and stated by the builder rather than assumed.

## What is left to the builder

Exact wording and page lengths; the choice of PDF library (within the constraint above); the heading heuristic; how the example
chooses `external_handle.system` and `native_id` and `doc_type`/`domain_tags` values; which recipes the query guide shows; the
bug-report field list (suggested starting set: title, kind, SMOS version from `/health`, what was done with the exact request, what
was expected, what happened with the exact response and status, what was already ruled out, token scope, whether it reproduces,
severity guess); where the test for the example lives.

## Verification

1. Start a real local SMOS on a temp data dir, create a token the way the guide says, and run every documented request; paste the
   real responses into the guide (trimmed, never invented).
2. Run the example end to end on a small made-up PDF generated in the test, then query it through the guide's recipes.
3. Check every internal link and every endpoint name against `/api/v1/openapi.json`.
4. Carried over from the previous session, written into the verification record as steps for the Governor on Debian: persistence
   across `docker compose down` / `up -d` (no `-v`), and a read-only token being refused (HTTP 403) on `POST /api/v1/documents`.
   The same refusal is also checked automatically against the local server.
5. A readability pass as a reader who has only the URL, a token and the docs: can they ingest and query without opening any other
   file? Findings fixed before handing over.
6. Codex review package afterwards, as for earlier projects, focused on accuracy of the docs against the code.

## Order of work

1. Plan (superpowers:writing-plans), then inline execution page by page, example first (it forces the facts), then guide pages,
   then README/API.md fixes, then the verification record and handoff.
2. Release: documentation is not shipped to installations by the updater (it lives outside `smos/`), and `pyproject.toml` is not
   touched, so no release is needed. Commit and push to `main` only. (Handoff caution still stands: the next real release is 0.3.3
   or higher.)
