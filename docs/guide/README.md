# SMOS guide

SMOS is a self-hosted store that keeps documents exactly and returns the relevant passages by meaning and keyword, over an
authenticated HTTP API. It never generates text. This guide is for the people and agents who feed it and query it.

| If you are... | Read, in this order |
|---|---|
| New to SMOS | [concepts.md](concepts.md) |
| Writing a parser that feeds SMOS | [concepts.md](concepts.md), [parser-author-guide.md](parser-author-guide.md), then run `docs/examples/standards_pdf_parser.py` |
| An agent or person querying SMOS | [concepts.md](concepts.md), [query-guide.md](query-guide.md), [agent-playbook.md](agent-playbook.md) |
| Hitting an error, a surprise, or having an idea | [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md), [known-limits-and-roadmap.md](known-limits-and-roadmap.md) |
| Running the server | [../INSTALL.md](../INSTALL.md) |

## Pages

- [concepts.md](concepts.md): what SMOS is and is not, the glossary, the shape of a stored document.
- [parser-author-guide.md](parser-author-guide.md): the ingestion contract, how blocks become segments, links, re-ingest, quarantine, a
  worked standards-PDF parser.
- [query-guide.md](query-guide.md): which endpoint to use, every parameter, how to read a result, recipes.
- [agent-playbook.md](agent-playbook.md): one page of rules and flows for an agent.
- [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md): symptoms, causes, bug-report and feature-request templates.
- [known-limits-and-roadmap.md](known-limits-and-roadmap.md): behaviors that surprise, with the workaround and the planned direction.

## Quick start

1. Get a token (the admin creates an invitation, you open the link once; see [../API.md](../API.md#authentication)).
2. Check the server: `curl -s http://localhost:8765/api/v1/health`.
3. Ingest: `python docs/examples/standards_pdf_parser.py --pdf your.pdf --system std-pdf --designation "ACME 1000" --name "ACME 1000" --url http://localhost:8765`.
4. Ask:

```bash
curl -s http://localhost:8765/api/v1/query -H "Authorization: Bearer $SMOS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "maximum permitted widget deflection", "k": 3}'
```

The authoritative, generated description of every endpoint is `GET /api/v1/openapi.json` (needs a token); the reference summary is
[../API.md](../API.md). Evidence for the statements in this guide is in [../guide-verification.md](../guide-verification.md).

*Future:* these pages will be served through the API; for now they are files in `docs/`.
