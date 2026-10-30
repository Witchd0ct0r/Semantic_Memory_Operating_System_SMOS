# Agent playbook

One page for an agent that has a SMOS URL and a token. Details are in [query-guide.md](query-guide.md) and
[parser-author-guide.md](parser-author-guide.md).

## Rules

1. **Use the least token.** Looking things up needs only a read-only token. Use a read/write token only to ingest or write notes.
2. **Quote, never paraphrase, when you cite.** `content` is the exact stored text. Give the user the quote, the `metadata.doc_id`
   and, if the text starts with a clause number, that number.
3. **A result is not an answer.** SMOS returns the nearest passages even when none is relevant. Read `score`, read the text,
   and say "not found" when nothing answers the question.
4. **Never invent ids.** Use only ids SMOS returned (segment ids are UUIDs, document ids are 16 hex characters).
5. **Check `status` on every ingest.** HTTP 200 can mean `quarantined`.
6. **Do not keep secrets in notes.** Everything you store is readable by every token holder.

## Look something up

1. `POST /api/v1/query` with a full sentence (at least 10 characters), `k` 5.
2. Keep results that are close (`score` clearly above the unrelated floor of about 0.2) and whose `metadata.tags` fit the task.
3. To read around a hit: `GET /api/v1/nodes/{metadata.doc_id}/text`, or `POST /api/v1/recall` with `strategy: "exact"`.
4. To see related documents: `POST /api/v1/graph/query` with the `doc_id`, `max_hops` 1.
5. Answer with quotes and ids. If nothing fits, say so.

## Load a document

1. Make blocks: one per clause, heading with its text, each ending with `"\n"`.
2. `POST /api/v1/documents`; check `status == "success"`; keep the `doc_id`.
3. Verify: `GET /api/v1/nodes/{doc_id}/text` equals your joined blocks; query one sentence and expect it back.
4. If `quarantined`: fix what `errors` names and resend the whole payload.

## When something goes wrong

| You see | Do |
|---|---|
| 401 `valid bearer token required` | The token is missing, wrong, revoked, expired (they last 30 days) or rotated away. Ask the admin; do not retry in a loop. |
| 403 `this token is read-only` | You need a read/write token for this call. |
| 400 with a `detail` text | Fix the input it names (query shorter than 10 characters, unknown strategy or edge type, unknown source id). |
| 422 | The JSON does not match the schema; read `detail[].loc` and `msg`. |
| 404 `node not found` / `memory not found` | The id is not stored (it may have been replaced by a re-ingest). |
| 503 | SMOS is applying an update (writes refused for about a minute); reads keep working. Wait and retry once. |
| Results look wrong | Do not guess. Follow [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md) and report with evidence. |

## When to stop and report

Stop and write a report (template in [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md#bug-report)) when a request
that matches this guide behaves differently from this guide, when the same input gives different results on repeat, when stored text
differs from what you sent, or when you need a feature SMOS lacks ([known-limits-and-roadmap.md](known-limits-and-roadmap.md)).
