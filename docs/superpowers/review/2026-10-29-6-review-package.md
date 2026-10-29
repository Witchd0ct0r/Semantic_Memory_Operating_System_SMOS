# Review package 6: Project 5, documentation (guide, example parser, guard tests)

Sixth review round. Earlier: package 5 (`c042d4c..HEAD` at the time, resolved in `docs/update-verification.md` section 5). This
package covers the documentation project: `git diff 2fbd79b..HEAD` (design `docs/superpowers/specs/2026-10-29-documentation-design.md`,
plan `docs/superpowers/plans/2026-10-29-documentation.md`). **No SMOS behavior was changed**: nothing under `smos/`, `pyproject.toml`,
`Dockerfile` or `compose.yaml` is in the range. The job is to find places where the documentation is **wrong, misleading or
untested** against the code. Style nits are not wanted. Report only; do not edit.

## 1. Scope

- `docs/guide/` (7 pages), `docs/guide-verification.md` (real run record), `docs/examples/` (`standards_pdf_parser.py`,
  `requirements.txt`, `README.md`), `README.md` and `docs/API.md` (stale text refreshed).
- Tests: `tests/test_example_standards_pdf.py` (20 tests: example units, plus behavior pins against a real in-process app),
  `tests/test_docs_guide.py` (links, endpoint names, every endpoint documented), `tests/helpers.py::make_pdf` (hand-built PDF).

## 2. Run it

```bash
pip install -r docs/examples/requirements.txt          # pypdf, example only; the tests skip without it
rtk proxy python -m pytest tests -q                    # expect 776 passed (about 100 s)
```

## 3. What to attack, hardest first

1. **Claims that do not match the code.** Take each factual sentence in `parser-author-guide.md`, `query-guide.md` and
   `known-limits-and-roadmap.md` and check it in `smos/` (`ingest/contract.py`, `ingest/segmenter.py`, `ingest/link_wiring.py`,
   `tools/ingest_tools.py`, `compression/context_builder.py`, `api/routes.py`, `api/models.py`, `memory/requirement_context.py`).
   Especially: the segmentation rules (800 / 20 characters, previous-vs-next merge), "sections only from split blocks", the list of
   accepted-but-ignored fields, the edge-type list, placeholder link nodes, link replacement on re-ingest, the `tags` fallback, the
   normative rule, the error texts and status codes, the `exact`/`temporal`/`repo`/`chain` descriptions.
2. **Examples that were not really run.** `docs/guide-verification.md` is the source for every response shown. Look for numbers or
   texts in the guide that are not in that record (score values, offsets, ids, `confidence`).
3. **The example parser's edge cases.** `to_blocks` (heading carry-forward, a heading-like body line such as `2 Millimetres is the
   limit`), `strip_furniture` (only for 3+ pages, "more than half"), `find_references` (prefix list, spacing, own designation),
   `main` exit codes, `post_document` error handling, a PDF with extractable text but odd encodings.
4. **Tests that give false comfort.** `test_docs_guide.py` link/anchor slug rules against GitHub's, the endpoint regex (does it miss
   `PATCH`, paths with query strings?), whether "every endpoint documented" could pass vacuously; the behavior pins (do they fail if
   SMOS changes?), `make_pdf` correctness.
5. **Anything the docs hide that a parser author will hit.** Compare with the contract and the segmenter: payload limits, what
   happens with very long single lines, duplicate `native_id` in different systems, unicode, tables as blocks, concurrency of two
   parsers writing the same document.
6. **Safety of the example.** It sends a bearer token read from an environment variable to a URL given on the command line; it
   builds no shell commands; check redirects and error output for token leaks.

## 4. Known limits and honest gaps

- The docs describe SMOS as it is. Four behaviors that contradict the written API description or look like defects are recorded as
  technical debt, not fixed: `tags` on `/query` is a preference, no adapter is selected over the API (no reference detection),
  ignored contract fields, normative scope hard-wired to `doctype:legal|standard`.
- No reader who has never seen SMOS has used the guide yet; the readability pass was done by the author
  (`docs/guide-verification.md` section 5).
- The Governor's two Debian steps (persistence across `down`/`up`, read-only token refused) are written down in section 4 of the
  record but not yet run on Debian; the refusal is also asserted by `test_read_only_token_cannot_ingest`.
- Docs are not served through the API yet and the tickets feature does not exist; both are noted as future work in the guide.

## 5. How to report

Per finding: severity (Critical / Important / Minor), the file and line of the doc or test, the sentence or assertion in question,
the code or run that contradicts it, and a one- or two-sentence fix. Write the report to
`docs/superpowers/review/2026-10-29-6-review.md`.
