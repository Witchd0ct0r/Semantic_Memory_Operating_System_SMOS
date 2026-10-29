# Parser author guide

For anyone (human or agent) writing a program that reads a source and feeds SMOS. You need: the server URL, and a
**read/write token** (see [Getting a token](../API.md#authentication)). Terms are in [concepts.md](concepts.md).

All examples below were produced by a real run; see [../guide-verification.md](../guide-verification.md).

## The flow

1. Read your source and cut it into **blocks** of text.
2. Build the JSON payload (below).
3. `POST /api/v1/documents` with `Authorization: Bearer <token>`.
4. Read the answer: `success` with a `doc_id`, `quarantined` with errors, or an HTTP error.
5. Check it by querying ([query-guide.md](query-guide.md)) and, if you like, `GET /api/v1/nodes/{doc_id}/text`.

## The payload

```json
{
  "external_handle": {"system": "std-pdf", "native_id": "ACME 1000"},
  "doc_name": "ACME 1000 Widgets",
  "blocks": [
    {"block_type": "paragraph", "text": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n"},
    {"block_type": "paragraph", "text": "2.1 Deflection\nThe maximum permitted deflection shall not exceed two millimetres.\n"}
  ],
  "doc_type": "standard",
  "domain_tags": ["widgets"],
  "source_links": [
    {"target_native_id": "ISO 9001", "edge_type": "references", "metadata": {"target_system": "std-pdf"}}
  ],
  "summary": "Optional summary you wrote.",
  "summary_source": "my-parser 1"
}
```

| Field | Rules |
|---|---|
| `external_handle.system`, `.native_id` | Required, not empty. Together they are the document's identity: the same pair always means the same document. |
| `blocks[]` | Required, at least one. `text` must not be empty or only spaces. `block_type` is `heading`, `paragraph`, `list` or `table`. A `heading` needs `heading_level` 1 to 6. Total text of all blocks: at most 5,000,000 characters. |
| `doc_name` | Optional display name (shown in evidence output). |
| `doc_type` | Optional, free text, e.g. `requirement`, `legal`, `standard`. It becomes the tag `doctype:<doc_type>` on every segment. `legal` and `standard` also mark a document as **normative** evidence for `/requirement-context`. |
| `domain_tags[]` | Optional. A tag must not contain a comma. Use them to scope queries (see [query-guide.md](query-guide.md#tags)). |
| `source_links[]` | Optional links to other documents: see [Links](#links). |
| `metadata{}` | Optional free-form object, kept on the document node (returned by `/graph/query`). Put things SMOS has no field for here, such as a deep link or a source version. |
| `summary`, `summary_source` | Optional caller-written summary of the whole document. |

**Accepted but not stored or used:** `external_handle.deep_link`, `.version`, `.source_updated_at`, block `source_anchor`,
`schema_version`, `confidence`, `transformer_name`, `transformer_version`, `ingested_at`. They are validated for type only. Keep
anything you need later in `metadata{}`. See [known limits](known-limits-and-roadmap.md#accepted-but-ignored-fields).

## Blocks must end with a newline

SMOS joins block texts with **nothing** in between. Two blocks `"First line."` and `"Second line."` reconstruct to
`First line.Second line.` (verified). End every block text with its own `"\n"` so the stored text is readable and clauses stay apart.

## How blocks become segments

SMOS cuts the joined text into **segments**, the units it embeds and returns:

1. Every block is a candidate segment. Block types and heading levels are not used for cutting.
2. A block longer than **800 characters** is split by sentences (then by size). The pieces become several segments and SMOS
   adds a **section** node for that block.
3. A block shorter than **20 characters** is merged into the **previous** segment (a tiny block at the very start is merged into the next one).

Consequences for you:

- **Keep a heading together with its text.** A bare block `"2.2 Marking\n"` (12 characters) is glued to the *end of the previous
  clause's segment*, and the clause it names then starts without its heading (verified in the run). Put the heading line and the
  first paragraph into one block, as `docs/examples/standards_pdf_parser.py` does.
- **One clause, one block** gives one segment per clause up to about 800 characters, which is what a query then returns.
- A heading with no body of its own can travel with the next block (`"2 Limits\n2.1 Deflection\nText...\n"`).
- Sections are **not** derived from headings. Do not rely on `section_count` to reflect your document structure.

## Links

`source_links` connect this document to others. Each link becomes an edge `this document --edge_type--> target document`.

- `edge_type` must be one of: `references`, `depends_on`, `related_to`, `implements`, `contains`, `evolves_into`,
  `generated_from`, `parent_of`, `member_of`, `cites`. Anything else is quarantined.
- The target is identified like any document: `target_native_id` in system `metadata.target_system` (default: this document's own
  system). `metadata` may carry more keys (for example a cited `clause`); they are stored on the edge.
- A link to its own document is rejected.
- **The target need not exist yet.** SMOS creates a placeholder document node with the target's deterministic id, and ingesting the
  target later fills that same node.
- A document's links are **replaced wholesale** each time you re-ingest it.
- **SMOS does not find references in your text.** Over the API no reference detection runs (see
  [known limits](known-limits-and-roadmap.md#adapter-selection)). If clause 4 says "see ISO 26262-6", your parser must read that and
  declare the link.

## Re-ingesting

<a id="re-ingesting"></a>

Posting the same `external_handle` again is an **upsert**: same `doc_id`, the old segments and the document's own edges are replaced,
new segment ids are issued, and nothing of the old text remains. Do this whenever the source changes. Documents other than
this one keep their links *to* it.

Deleting a whole document is not available through the API in 0.3.x; re-ingest it with changed content instead, or delete single
memories with `DELETE /api/v1/memories/{id}`.

## Quarantine

<a id="quarantine"></a>

A payload that matches the schema but breaks a content rule is **not rejected**. SMOS answers **HTTP 200**:

```json
{"status": "quarantined", "quarantine_key": "ffc57fb0-...", "errors": ["blocks[0]: text must not be empty"]}
```

Real error texts: `blocks[i]: text must not be empty`, `blocks[i]: heading blocks require heading_level in 1..6`,
`source_links[i]: unknown edge_type 'bogus'`, `source_links[i]: link targets its own item`,
`domain_tags[i]: must not contain ','`, `external_handle.system: must not be empty`, `blocks: must contain at least one block`,
`payload: total block text N exceeds max 5000000`.

- **Always check `status`.** A 200 does not mean stored.
- Nothing from the payload is stored as a document; SMOS keeps a copy under `quarantine_key` but offers no endpoint to read it
  back, so keep your own copy and resend after fixing.

A payload that does not match the schema at all (a missing `blocks`, a wrong type) returns **HTTP 422** with FastAPI's
`{"detail": [{"loc": [...], "msg": "..."}]}`. A read-only token gets **403**, a missing or wrong token **401**.

## Big sources

The cap is 5,000,000 characters of block text per document. For anything larger split the source into several documents (for
example one per chapter, with `native_id` such as `ACME 1000/ch3`) and link them with `parent_of` / `member_of` / `references`.
Post documents one at a time; the same document is processed one request at a time, different documents in parallel.

## Pre-flight checklist

- [ ] `system` and `native_id` are stable across runs of your parser (they decide idempotency).
- [ ] Every block ends with `"\n"` and none is empty.
- [ ] Each heading travels with its body.
- [ ] No tag contains a comma; `doc_type` is set (`standard`, `legal`, `requirement`, ...).
- [ ] References in the text are declared in `source_links` with the right `edge_type` and `target_system`.
- [ ] You check `status` in the response, not just the HTTP code.
- [ ] After ingest: `GET /api/v1/nodes/{doc_id}/text` equals your joined blocks, and a query for a sentence you know returns it.

## Worked example: a standards PDF

`docs/examples/standards_pdf_parser.py` reads a **text** PDF of a standard and posts it. It is a teaching example, not a general
PDF parser.

```bash
pip install -r docs/examples/requirements.txt
export SMOS_TOKEN=smos_...            # a read/write token
python docs/examples/standards_pdf_parser.py --pdf acme1000.pdf --system std-pdf \
    --designation "ACME 1000" --name "ACME 1000 Widgets" --tag widgets --url http://localhost:8765
# prints: 3 block(s), 1 link(s)   then   status=success doc_id=... segments=3
```

What it does, in order:

1. `extract_pages`: text per page with `pypdf`; a PDF with no text (a scan) stops with an error and posts nothing. There is no OCR.
2. `strip_furniture`: drops bare page numbers and, for PDFs of 3 or more pages, lines repeated on more than half of the pages
   (running headers and footers).
3. `to_blocks`: lines like `5.2.1 Title` (a number, then a capitalised title, no final full stop) start a clause. A heading and
   the paragraph under it become one block; wrapped lines are joined with a space.
4. `find_references`: designations such as `ISO 26262-6`, `IEC 61508`, `EN 1090`, `DIN 1234` (other than the document's own) become
   `references` links to documents in the same system.
5. `build_payload` and `post_document`: the payload as above, then the POST. `--dry-run` shows block and link counts without posting;
   exit code 0 = stored, 2 = quarantined (errors printed), 1 = any other error.

Its limits, so you know what to improve for a real corpus: headings are guessed from numbering; tables, figures and footnotes are
plain text; hyphenated line breaks are not repaired; reference detection knows only four designation prefixes; a heading-like line
that starts a sentence with a number and a capital ("2 Millimetres is the limit") is taken for a heading.
