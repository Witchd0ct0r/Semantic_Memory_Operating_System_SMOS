# Troubleshooting and reporting

First find out what is wrong, then write it down so someone else can act on it.

## What you can check yourself

1. **Is it up, and which version?** `GET /api/v1/health` needs no token: `{"status":"ok","version":"0.3.1"}`. No answer means the
   server or the network, not the API.
2. **Is your token valid?** Any call except health answers 401 for a missing, wrong, revoked, expired or rotated-away token (tokens last 30 days). A read-only token gets 403 on
   writes.
3. **Is the request what you think?** Print the exact JSON you send and the exact status and body you get back.
4. **Is it already known?** Read [known-limits-and-roadmap.md](known-limits-and-roadmap.md).
5. **Can you make it smaller?** The smallest request that still shows the problem is the best evidence.

## Symptom, cause, action

| Symptom | Likely cause | Action |
|---|---|---|
| Ingest returns 200 but a search finds nothing | The payload was quarantined (`status: "quarantined"`) | Read `errors`, fix, resend ([parser guide](parser-author-guide.md#quarantine)) |
| A clause comes back with the next clause's heading at its end | A heading block under 20 characters was merged into the previous segment | Put the heading in the same block as its text |
| A clause comes back cut in two | The block was over 800 characters and was split | Expected; read the neighbours with `strategy: "exact"` or `GET /nodes/{id}/text` |
| `section_count` is 0 although the document has headings | Headings do not create sections | Expected ([concepts](concepts.md)) |
| A query with `tags` returns items without that tag | Tags are a preference, not a filter | Known limit; filter on `metadata.tags` yourself |
| A query for nonsense returns results | A query always returns its nearest items | Apply your own `score` threshold |
| References in the text are not linked | SMOS does not detect references over the API | Declare them in `source_links` |
| `link targets its own item` | A link names the document itself | Remove it; links connect different documents |
| 422 on ingest | The JSON does not match the schema | Read `detail[].loc` |
| 401 after it worked | The admin revoked the token, it passed its 30-day expiry, or the admin rotated it | Ask the admin: a rotated or expired token is replaced through a new claim link |
| 503 on writes | An update is being applied | Wait a minute and retry |
| Search results changed after re-ingest | Segment ids are new, the old text is gone | Use `doc_id` plus `ordinal` as a stable address |

## Bug report

<a id="bug-report"></a>

Write one report per problem. Every field below matters; "it does not work" cannot be acted on.

```text
Title:            one line: what is wrong, where
Kind:             bug
SMOS version:     from GET /api/v1/health
Token scope:      read | rw
What I did:       the exact request (method, path, JSON body; secrets removed). Smallest version that still fails.
What I expected:  and why (point to the guide page or sentence that says so)
What happened:    the exact status code and response body (or the missing/wrong part)
Already ruled out: what you tried (smaller input, other endpoint, different token, repeat run)
Reproduces:       always | sometimes | once
Impact:           what you cannot do because of it; a workaround if you have one
Severity guess:   blocks my work | wrong result | cosmetic
```

An agent should add: the id(s) involved, the text it sent and the text it got back, and whether the stored text still equals what
it sent (`GET /nodes/{doc_id}/text`).

## Feature request

```text
Title:            one line: what you want to be able to do
Kind:             feature
SMOS version:     from GET /api/v1/health
The need:         the task you are trying to do and who needs it
What I do today:  the workaround and what it costs
What I would like: the behavior in terms of requests and responses (e.g. "POST /query accepts min_score and drops weaker results")
Not wanted:       what would make it worse (e.g. changing result order)
Related limits:   a link to the entry in known-limits-and-roadmap.md, if any
```

## Where to send it

Until SMOS has a tickets feature, give the report to the SMOS admin, or open an issue in the project's repository if you have access.

## Future

<a id="future"></a>

Reference for later; none of this exists yet, and these docs will be updated when it does.

- **Tickets API and admin panel:** token holders will be able to post a bug report or feature request through the API, and the admin
  will review them in a dashboard panel. The two templates above use plain named fields so they can become the request body
  unchanged.
- **Serving these docs through the API:** an agent with only the URL and a token will be able to read this guide from the server.
