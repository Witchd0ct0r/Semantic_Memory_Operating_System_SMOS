# Concepts

What SMOS is, what it is not, and the words the rest of the guide uses.

## What SMOS is

A self-hosted store that keeps documents **exactly** and finds passages by meaning and by keyword, behind an authenticated HTTP API.

- **It stores text as given.** `GET /api/v1/nodes/{doc_id}/text` returns the document byte for byte (the concatenation of your blocks).
- **It never generates text.** It does not summarise, paraphrase or judge. A parser or agent may attach summaries; SMOS stores them
  and returns them beside the exact text.
- **It finds passages.** A query returns ranked segments (exact stored text) with their ids, scores and a few metadata fields.
- **It keeps a graph** of documents, sections, segments and the links parsers declare between documents.

It is built for corpora such as standards, regulations and requirements, where a local agent needs the right passage with its
source, not a paraphrase.

## What SMOS is not

- Not a parser: it does not read PDFs, Word files or web pages. A **parser** (yours) turns a source into the JSON contract.
- Not a language model: it has no built-in AI and calls none.
- Not a document viewer. The admin pages show usage and access, not content.

## Glossary

| Word | Meaning |
|---|---|
| **Parser** | Your program (or agent) that reads a source and posts it to SMOS. See [parser-author-guide.md](parser-author-guide.md). |
| **Document** | One ingested item, identified by `external_handle {system, native_id}`. `doc_id = sha1("system:native_id")[:16]`. |
| **Block** | A piece of text in the ingestion JSON (`blocks[]`). Blocks are joined with no separator to form the document text. |
| **Segment** | The unit SMOS stores, embeds and returns. Cut from the block text at block boundaries and by size (see [the parser guide](parser-author-guide.md#how-blocks-become-segments)). |
| **Section** | A graph node SMOS creates only when one block was long enough to be split. Headings do not create sections. |
| **`doc_id`, segment id** | Document ids are 16 hex characters; segment and memory ids are UUIDs. Every result carries `metadata.doc_id`. |
| **Tags** | `domain_tags` you set on a document, plus `doctype:<doc_type>`. They are stored on every segment as a comma-joined string. |
| **Summary** | Text *you* wrote for a document, section or segment. Returned as `summary`, `section_summary`, `document_summary`. |
| **Edge / link** | A typed connection between two nodes (`references`, `depends_on`, ...). Parsers declare document-to-document links in `source_links`. |
| **Quarantine** | Where a payload that parses but breaks the content rules is kept; the response says so with HTTP 200. |
| **Token** | A bearer secret, `read` (look up only) or `rw` (read and write). Obtained through a one-time invitation link. |
| **Memory** | A free-text item stored with `POST /memories` (notes, decisions). Segments of documents are memories of type `segment`. |

## Shape of a stored document

```text
document  "ACME 1000"            doc_id 67e53410c17a5649   (graph node)
 |- segment 0  "1 Scope ..."     ordinal 0, char_start 0, char_end 65
 |- segment 1  "2 Limits ..."    ordinal 1
 `- segment 2  "2.2 Marking ..." ordinal 2
 `- [section nodes only when a single block was split into several segments]
document --references--> document "ISO 9001"   (declared in source_links)
```

`ordinal`, `char_start` and `char_end` locate a segment inside the reconstructed document text.

## Who reads what

See [README.md](README.md) for the page order for parser authors, querying agents and people reporting problems, and
[known-limits-and-roadmap.md](known-limits-and-roadmap.md) for behaviors that surprise people.
