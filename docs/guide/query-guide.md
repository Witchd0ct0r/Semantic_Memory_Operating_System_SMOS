# Query guide

How to get passages back out of SMOS. A read-only token is enough for everything on this page except where noted. Real responses are
in [../guide-verification.md](../guide-verification.md); terms in [concepts.md](concepts.md).

## Which endpoint?

| You want | Use |
|---|---|
| The passages most relevant to a question | `POST /api/v1/query` |
| The same, plus neighbouring segments for context | `POST /api/v1/recall` with `strategy: "exact"` |
| Everything stored in a time window | `POST /api/v1/recall` with `strategy: "temporal"` |
| Items carrying a tag | `POST /api/v1/recall` with `strategy: "repo"` and `component` |
| What a stored note was derived from, hop by hop | `POST /api/v1/recall` with `strategy: "chain"` and `seed_id` |
| A whole document or section, byte for byte | `GET /api/v1/nodes/{id}/text` |
| What a document links to, or what links to it | `POST /api/v1/graph/query` |
| Evidence for judging a requirement (linked, similar, normative) | `POST /api/v1/requirement-context` |
| A lossless blob you stored | `GET /api/v1/verbatim/{key}` |

All requests: `Authorization: Bearer <token>`, `Content-Type: application/json`.

## `POST /api/v1/query`

```bash
curl -s http://localhost:8765/api/v1/query -H "Authorization: Bearer $SMOS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "maximum permitted widget deflection", "k": 3}'
```

| Parameter | Rules |
|---|---|
| `query` | Required; at least 10 characters (shorter returns HTTP 400 `query must be at least 10 characters`). |
| `k` | 1 to 100, default 5 (above 100 returns 422). |
| `tags` | Optional list. A **preference**, not a filter: see [known limits](known-limits-and-roadmap.md#tags-on-query-is-a-preference-not-a-filter). |

Real answer (trimmed):

```json
{
  "results": [
    {"id": "6e263a94-...", "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
     "summary": null, "section_summary": null, "document_summary": null,
     "score": 0.8096,
     "metadata": {"type": "segment", "tags": "widgets,doctype:standard", "doc_id": "67e53410c17a5649",
                  "ordinal": 1, "char_start": 56, "char_end": 154}}
  ],
  "sources": ["6e263a94-...", "..."],
  "confidence": 0.80,
  "mode": "extractive"
}
```

How to read it:

- `content` is the **exact stored text**. Quote it as is. Never present a paraphrase as a quotation.
- `score` is closeness (higher is closer). Direct hits scored about 0.75 to 0.81 and unrelated text 0.02 to 0.18 in the real run.
  **A query always returns its nearest items**, however weak; apply your own threshold.
- `summary`, `section_summary`, `document_summary` are text a parser supplied for the segment, its section and its document
  (`null` when none, with matching `*_source` fields saying who wrote them).
- `metadata.doc_id` says which document; `ordinal`, `char_start`, `char_end` say where in that document's text. `tags` is a
  comma-joined string: your `domain_tags` plus `doctype:<doc_type>`.
- `sources` repeats the result ids in rank order; `confidence` is an overall figure for the list, not a probability of being right.
- `mode` is `extractive` normally and `uncertain` only when nothing was retrieved at all.

Search combines meaning (embeddings), keywords (BM25) and graph neighbours of the top hit, merged by rank. An exact term, clause
number or identifier is found by the keyword part even when the wording differs.

## Tags

Every segment carries the `domain_tags` of its document plus `doctype:<doc_type>`, as one comma-joined string in `metadata.tags`.
Use them to narrow what you accept: after a query, keep results whose `metadata.tags` contain your tag. Passing `tags` in the request
only nudges the search ([known limit](known-limits-and-roadmap.md#tags-on-query-is-a-preference-not-a-filter)), so do not rely on it to exclude.

## `POST /api/v1/recall`

Body: `query` (not needed for `temporal`, `chain`, `repo`), `strategy`, `k` (1 to 100; for `chain` the number of hops), `since`,
`until` (ISO-8601, for `temporal`), `seed_id` (for `chain`), `component` (for `repo`). An unknown strategy returns 400
`unknown strategy: magic`.

- `hybrid`: the same search as `/query` (result shape as above).
- `exact`: top segments in `segments`, plus `context` with the neighbouring segments (the segment before and after each hit) so you
  can read a clause with its surroundings. Real run: a query for "serial marking" returned the marking clause in `segments`.
- `temporal`: items stored between `since` and `until`. Hit `metadata` is empty for this strategy.
- `repo`: items whose tags include `component`.
- `chain`: follows links from `seed_id` for `k` hops; empty when the item has no links.

## Reading a whole document or section

`GET /api/v1/nodes/{id}/text` with a document id (or a section id) returns

```json
{"node_id": "67e53410c17a5649", "text": "1 Scope\nThis standard applies...", "segment_count": 3, "complete": true, "error": null}
```

`text` is the concatenation of the stored blocks, byte for byte; `complete` is `false` with an `error` if segments are missing.
404 means an unknown id. Use it after a search to read the surrounding document.

## Following links

`POST /api/v1/graph/query` with `{"term": "<doc_id>", "max_hops": 2}` (`max_hops` 1 to 6, default 2) returns nodes with `type`
(`memory` for segments, `document`, `section`, `entity`), `name`, `metadata` and `hop`. From a document id you get its segments at
hop 1, documents it links to (or that link to it) at hop 1, and those documents' segments at hop 2. Direction is ignored. A `term`
that is not a stored node id is treated as an entity name (`entity:<term>`); a standard's designation is a document, not an entity, so
derive its `doc_id` with `sha1("system:native_id")[:16]`.

## Evidence for a requirement

`POST /api/v1/requirement-context` gathers evidence for judging a requirement and gives **no verdict**.

```json
{"text": "The widget shall not deflect more than 3 mm.", "normative_tags": ["doctype:standard"]}
```

Give a draft `text`, or a stored item (`doc_id`, or `system` + `native_id`), or both. Optional: `level`, `domain_tags` (peers must share
one), `normative_tags`, `declared_links`, `token_budget` (at least 100, default 4000), `k_per_section` (1 to 50, default 5).
The answer has `linked`, `peers`, `normative` (each item: `doc_id`, `segment_id`, `doc_name`, `text`, `score`, `reason`, `clause`,
`source_kind`, `hops`, `path`, `clipped`), `coverage` (counts and `unresolved_links`) and `truncated`. In the real run a draft
requirement about "3 mm" returned the standard's two-millimetre clause first (`reason: "standard match"`, score 0.79).
Normative means `doctype:legal` or `doctype:standard` unless you pass `normative_tags`
([known limits](known-limits-and-roadmap.md#normative-means-doctypelegal-or-doctypestandard)).

## Writing back (needs a read/write token)

- `POST /api/v1/memories` stores a note: `type` (e.g. `doc`, `adr`, `log`, `semantic`), `content` (at least 10 characters), optional
  `tags`, `summary`, `summary_source`; returns `{"id": ...}`.
- `POST /api/v1/memories/derived` stores text you wrote (`kind` `compressed` or `reflection`) linked to `source_ids` that must exist.
- `PUT /api/v1/nodes/{id}/summary` attaches or replaces a summary on a memory, document or section (404 for an unknown id).
- `POST /api/v1/links` creates an edge between two existing ids (`id_a`, `id_b`, `edge_type`); an unknown type returns 400
  `invalid edge_type: bogus`.
- `DELETE /api/v1/memories/{id}` removes one memory with its edges and summary (404 when unknown).
- `POST /api/v1/verbatim` stores exact content (returns a `key`); `GET /api/v1/verbatim/{key}` returns it.

## Recipes

**Find the clause about X in a standard.** `POST /query` with a sentence, `k` 5, take results whose `score` is high and whose
`metadata.tags` contain `doctype:standard`; quote `content` and cite `metadata.doc_id` and the clause number at the start of `content`.

**Read the clause in context.** `POST /recall` with `strategy: "exact"`; read `context`, or `GET /nodes/{doc_id}/text` and cut with
`char_start` / `char_end`.

**Which other standards does this one cite?** `POST /graph/query` from its `doc_id`, `max_hops` 1, keep `type: "document"`.

**Is this requirement consistent with the standards?** `POST /requirement-context`; read `normative` first, then `linked`; decide
yourself (SMOS only gathers).
