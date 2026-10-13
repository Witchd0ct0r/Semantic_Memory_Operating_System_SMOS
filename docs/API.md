# SMOS API Reference

SMOS (Semantic Memory Operating System) is a local memory server. It exposes **22 MCP tools**
over stdio (server name `smos`, entry point `smos-server`) and a small **Python API** underneath
them. This document is the complete reference for both.

- [1. Concepts](#1-concepts)
- [2. Conventions](#2-conventions)
- [3. Tool index](#3-tool-index)
- [4. Memory write tools](#4-memory-write-tools)
- [5. Retrieval tools](#5-retrieval-tools)
- [6. Graph and agent primitives](#6-graph-and-agent-primitives)
- [7. File tools](#7-file-tools)
- [8. Repository ingestion tools](#8-repository-ingestion-tools)
- [9. Structured document ingestion](#9-structured-document-ingestion-tool_ingest_document)
- [10. Requirement context](#10-requirement-context-tool_requirement_context)
- [11. Data model](#11-data-model)
- [12. Python API](#12-python-api)
- [13. End-to-end recipes](#13-end-to-end-recipes)
- [14. Limits and gotchas](#14-limits-and-gotchas)

---

## 1. Concepts

| Term | Meaning |
|---|---|
| **Memory** | One stored text with a type, tags, tier, confidence and a vector embedding. Stored in SQLite (authoritative) and FAISS (index). |
| **Segment** | A memory of type `segment`: one exact slice of an ingested document (no summarisation), with `doc_id`, `ordinal`, `char_start`, `char_end`. |
| **Document** | A graph node of type `document` that owns a document's sections and segments through `contains` edges. Its id is `derive_doc_id(system, native_id)`. |
| **Graph** | SQLite node/edge layer next to the vector index. Node types: `entity`, `concept`, `memory`, `repo_component`, `snapshot`, `agent`, `document`, `section`. |
| **Hybrid retrieval** | FAISS vector search and BM25 fused with Reciprocal Rank Fusion, then optionally expanded one hop over `references` edges. |
| **Verbatim store** | A separate key-value store for exact content. No embedding, no compression. |
| **Quarantine** | Where a non-conforming ingestion payload is parked (with its validation errors) instead of being rejected. |
| **Placeholder** | A `document` node created for a link target that has not been ingested yet; the real ingest upgrades it. |

Each project gets its own isolated store in `<project root>/.smos/` (created on first use). The
server must be started with the project root as its working directory (Claude Code does this).

## 2. Conventions

**Transport.** MCP over stdio. Every tool takes flat, JSON-typed arguments. Lists are passed as
**comma-separated strings** (`tags="auth,security"`); structured arguments are passed as **JSON
strings** (`payload_json`, `declared_links_json`).

**Errors.** Tools never raise to the client. Failures come back as data:

| Tool kind | Failure shape |
|---|---|
| Returns a dict | the dict carries `"error": "<ExceptionName>: <message>"` (and tool-specific zero values) |
| Returns a string id | the *string form of a dict*, e.g. `"{'status': 'skipped', 'reason': 'invalid_input', 'field': 'text'}"` or `"{'error': '...'}"` |
| `tool_write_file_safe` | `{"success": false}` with no detail |

**Minimum input.** Free-text inputs (`text`, `content`, `query`) must contain at least **10
non-blank characters**; shorter input is skipped (`status: "skipped"`), not stored.

**Confidence and mode.** Summarising tools report `confidence` (0–1) and `mode`:
`abstractive` (LLM summary), `extractive` (exact stored text or first-sentences fallback), or
`uncertain` (nothing relevant, or the LLM was unreachable).

**LLM availability.** Compression uses a local model through Ollama. If Ollama is unreachable,
tools degrade to extractive summaries and keep working; semantic query and verbatim storage are
unaffected.

---

## 3. Tool index

| Group | Tool | Purpose |
|---|---|---|
| Write | [`tool_semantic_store`](#tool_semantic_store) | store text as a memory |
| Write | [`tool_semantic_write`](#tool_semantic_write) | store a typed, tagged memory |
| Write | [`tool_store_verbatim`](#tool_store_verbatim) | store exact content, get a key |
| Retrieve | [`tool_semantic_query`](#tool_semantic_query) | compressed context for a question |
| Retrieve | [`tool_recall`](#tool_recall) | retrieval with an explicit strategy |
| Retrieve | [`tool_retrieve`](#tool_retrieve) | fetch verbatim content by key |
| Retrieve | [`tool_reconstruct`](#tool_reconstruct) | rebuild an ingested document's exact text |
| Retrieve | [`tool_requirement_context`](#10-requirement-context-tool_requirement_context) | evidence pack for judging a requirement |
| Graph | [`tool_graph_query`](#tool_graph_query) | traverse the memory graph |
| Graph | [`tool_link`](#tool_link) | create an explicit edge |
| Agent | [`tool_remember`](#tool_remember) | store an episodic memory |
| Agent | [`tool_learn`](#tool_learn) | store a high-confidence semantic memory |
| Agent | [`tool_forget`](#tool_forget) | delete a memory and its edges |
| Agent | [`tool_compress`](#tool_compress) | merge memories into one |
| Agent | [`tool_reflect`](#tool_reflect) | reason over what is known and store the result |
| Agent | [`tool_snapshot`](#tool_snapshot) | snapshot the current project |
| Files | [`tool_read_file_compress`](#tool_read_file_compress) | read a file, store its summary |
| Files | [`tool_write_file_safe`](#tool_write_file_safe) | write inside the sandboxed workspace |
| Repo | [`tool_recursive_semantic_ingest`](#tool_recursive_semantic_ingest) | ingest a directory tree |
| Repo | [`tool_bulk_read`](#tool_bulk_read) | read many files in parallel |
| Repo | [`tool_semantic_snapshot_repo`](#tool_semantic_snapshot_repo) | full repository profile |
| Documents | [`tool_ingest_document`](#9-structured-document-ingestion-tool_ingest_document) | ingest a structured document payload |

---

## 4. Memory write tools

### `tool_semantic_store`

Store text as a semantic memory and return its id.

| Argument | Type | Default | Description |
|---|---|---|---|
| `text` | string | required | Content to store (≥ 10 non-blank chars). |
| `type` | string | `"doc"` | One of `doc`, `adr`, `log`, `issue`. |

**Returns** the memory id (UUID string), or the stringified skip/error dict.

### `tool_semantic_write`

Like `tool_semantic_store`, but with tags.

| Argument | Type | Default | Description |
|---|---|---|---|
| `type` | string | required | `doc`, `adr`, `log`, `issue`. |
| `content` | string | required | Content to store. |
| `tags` | string | `""` | Comma-separated tags, e.g. `"database,performance"`. |

**Returns** the memory id. Tags are what `tool_semantic_query(tags=...)` filters on.

### `tool_store_verbatim`

Store exact content with no summarisation and no embedding.

| Argument | Type | Default | Description |
|---|---|---|---|
| `content` | string | required | Exact content. |
| `label` | string | `""` | Human-readable label. |

**Returns** `{"key": str|null, "label": str, "bytes": int, "error": str|null}`.
Use it for code, diffs and anything where exact bytes matter; retrieve with
[`tool_retrieve`](#tool_retrieve).

---

## 5. Retrieval tools

### `tool_semantic_query`

Hybrid (vector + BM25) retrieval, compressed into one summary.

| Argument | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Natural-language question. |
| `k` | int | `5` | Results to compress (rarely need more than 10). |
| `tags` | string | `""` | Comma-separated tags; restricts the vector search to memories sharing at least one tag. Falls back to an unscoped search if the filter matches nothing. |

**Returns**

```json
{
  "summary": "Stores text with LLM-compressed embedding into FAISS...",
  "sources": ["f3a2b1c0-8d4e-4f7a-9b2c-1e5d6f3a2b1c"],
  "confidence": 0.91,
  "mode": "abstractive"
}
```

`sources` are memory ids; pass them to `tool_reconstruct`/`tool_recall(strategy="exact")` flows
when you need the original text. Recalled sources have their recall counter bumped (used by
tiering).

### `tool_recall`

Retrieval with an explicit strategy.

| Argument | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Query (ignored by `chain`). |
| `strategy` | string | `"hybrid"` | `hybrid`, `temporal`, `chain`, `repo`, `exact`. |
| `k` | int | `5` | Result count — or **hop depth** for `chain`. |
| `since` | string | `""` | ISO-8601 lower bound (`temporal`). Empty = unbounded. |
| `until` | string | `""` | ISO-8601 upper bound (`temporal`). |
| `seed_id` | string | `""` | Starting memory id (`chain`). |
| `component` | string | `""` | Tag to scope `repo` retrieval to. |

| Strategy | Behaviour | Returns |
|---|---|---|
| `hybrid` | Same pipeline as `tool_semantic_query`. | `{summary, sources, confidence, mode}` |
| `repo` | Hybrid, restricted to memories tagged `component`. | `{summary, sources, confidence, mode}` |
| `temporal` | Memories in `[since, until]`, ranked by similarity, compressed. | `{summary, sources, confidence, mode}` |
| `chain` | Follows `evolves_into` / `related_to` edges from `seed_id`. | `{sources, chain: [nodes], confidence: 1.0, mode: "extractive"}` |
| `exact` | **No compression.** Verbatim matching segments plus ±1 ordinal neighbours. | `{segments, context, sources, confidence, mode}` |

`exact` returns `segments` (the ranked matches, each `{id, content, metadata{doc_id, ordinal,
char_start, char_end, tags, ...}, score}`) and `context` (neighbouring segments, not repeated).
`confidence` is `1.0` whenever anything matched: it means "this is exact stored text", not "this
is relevant". An unknown strategy returns `{"error": "unknown strategy: ..."}`.

### `tool_retrieve`

| Argument | Type | Description |
|---|---|---|
| `key` | string | Key from `tool_store_verbatim`. |

**Returns** `{"key", "label", "timestamp", "content", "error": null}`, or
`{"key", "content": null, "label": null, "error": "not found"}`.

### `tool_reconstruct`

Rebuild the byte-exact text of an ingested document or section from its segments.

| Argument | Type | Description |
|---|---|---|
| `node_id` | string | A `document` node id (the `doc_id` returned by `tool_ingest_document`) or a section id (`<doc_id>:section:N`). |

**Returns** `{"node_id", "text", "segment_count", "complete", "error"}`. `complete` is `false`
when segments have a gap (for example an ingest still in progress); the text may then not be
byte-exact. An unknown id, or a node that is not a document/section, sets `error`. A placeholder
document (link target not yet ingested) reconstructs to `""`.

---

## 6. Graph and agent primitives

### `tool_graph_query`

| Argument | Type | Default | Description |
|---|---|---|---|
| `term` | string | required | An exact node id, or an entity name (resolved to `entity:<term>`). |
| `max_hops` | int | `2` | Traversal depth. Edges are treated as undirected. |

**Returns**

```json
{
  "term": "JWT",
  "start_id": "entity:JWT",
  "nodes": [{"id": "...", "type": "memory", "name": "...", "metadata": {}, "hop": 1}],
  "count": 7
}
```

Nodes are sorted by `hop`. Entities are extracted automatically from stored code (Python AST)
and from backtick-marked names in prose.

### `tool_link`

Create an explicit edge between two existing graph nodes.

| Argument | Type | Description |
|---|---|---|
| `id_a` | string | Source node id. |
| `id_b` | string | Target node id. |
| `edge_type` | string | Must be in the active vocabulary (see [11.4](#114-edge-vocabulary)). |

**Returns** `{"success": bool, "error": str|null}`. Fails for an unknown edge type or if either
node does not exist.

### `tool_remember`

Store an episodic memory. `content` (≥ 10 chars), `type` default `"episodic"`.
**Returns** the memory id.

### `tool_learn`

Store a high-confidence semantic memory (`type="semantic"`, `confidence=1.0`). `content` ≥ 10
chars. **Returns** the memory id.

### `tool_forget`

| Argument | Type | Description |
|---|---|---|
| `memory_id` | string | Memory to delete (its graph edges are removed too). |

**Returns** `{"success": bool, "error": str|null}`.

### `tool_compress`

Merge several memories into one compressed memory. Sources are **kept**; the new memory links to
them through `source_ids` and `generated_from` edges.

| Argument | Type | Description |
|---|---|---|
| `ids` | string | Comma-separated memory ids. |

**Returns** the new memory id (`""` if no source id exists).

### `tool_reflect`

Reason over the `k` memories most relevant to a question and store the reflection.

| Argument | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Question about the agent's own knowledge (≥ 10 chars). |
| `k` | int | `5` | Memories to reflect over. |

**Returns** the id of the stored `reflection` memory (`""` if nothing relevant exists).

### `tool_snapshot`

Take a [`tool_semantic_snapshot_repo`](#tool_semantic_snapshot_repo) of the current project
root. No arguments; same return shape.

---

## 7. File tools

### `tool_read_file_compress`

Read a file, compress it with the local LLM, store the summary, and return it. The raw file
never enters the context window.

| Argument | Type | Description |
|---|---|---|
| `path` | string | Absolute path, or a path relative to the sandbox workspace (`.smos/workspace`). |

**Returns** `{"summary": str|null, "id": str|null, "source": str, "error": str|null}`. The stored
memory is `type="doc"`, tagged `["file", <path>]`. Binary or non-UTF-8 files return an error.

### `tool_write_file_safe`

Write a file inside the sandboxed workspace (`.smos/workspace`). Traversal (`..`) and null bytes
are blocked; every write is appended to `.smos/logs/writes.jsonl`.

| Argument | Type | Description |
|---|---|---|
| `path` | string | Relative path inside the workspace. |
| `content` | string | File content (UTF-8). |

**Returns** `{"success": bool}`.

---

## 8. Repository ingestion tools

### `tool_recursive_semantic_ingest`

Scan a directory tree and ingest every supported text file.

| Argument | Type | Default | Description |
|---|---|---|---|
| `path` | string | required | Absolute directory path. |
| `recursive` | bool | `true` | Walk subdirectories. |
| `include_patterns` | string | `""` | Comma-separated globs; only matches are ingested. Empty = all supported. |
| `exclude_patterns` | string | `""` | Comma-separated globs to skip. |
| `max_files` | int | `5000` | Hard cap per call. |
| `summarize` | bool | `true` | `true`: LLM-compress each file (slow, best). `false`: store the first 500 characters (fast). |
| `store_raw_metadata` | bool | `true` | Tag memories with file size and extension. |

**Returns** `{"status", "files_scanned", "files_ingested", "files_skipped", "duplicates_removed",
"time_seconds", "memories_created"}` (or `{"status": "error", "error": ...}`).

Supported extensions: `.py .js .ts .tsx .jsx .java .go .rs .c .cpp .h .hpp .md .txt .rst .yaml
.yml .json .toml .ini .cfg`. Skipped directories: `.git node_modules venv .venv __pycache__
.pytest_cache build dist target .tox .mypy_cache .ruff_cache .eggs .cache .idea .vscode`.
Re-running on the same directory skips files already ingested.

> This tool stores **lossy summaries** of code. For structured prose that must be recalled
> exactly, use [`tool_ingest_document`](#9-structured-document-ingestion-tool_ingest_document).

### `tool_bulk_read`

Read many files in parallel (16 readers), no compression or storage.

| Argument | Type | Description |
|---|---|---|
| `paths` | string | Comma-separated absolute paths. |

**Returns**

```json
{
  "status": "success",
  "paths_requested": 3,
  "paths_read": 3,
  "time_seconds": 0.012,
  "results": [
    {"path": "/repo/a.py", "content": "...", "size_bytes": 1420, "error": null}
  ]
}
```

Result order matches input order. `error` is `"not_found"`, `"binary"`, or an OS error string.

### `tool_semantic_snapshot_repo`

Profile a repository and store the profile as a queryable memory.

| Argument | Type | Description |
|---|---|---|
| `path` | string | Absolute repository root. |

**Returns** `{"repository_path", "generated_at", "language_breakdown", "file_count",
"total_size_bytes", "major_modules", "important_files", "dependencies", "import_graph_edge_count",
"import_graph_sample", "architecture_summary", "memories_created", "snapshot_memory_id",
"time_seconds"}`.

Retrieve later with `tool_semantic_query(query="repository architecture", tags="snapshot")`.

---

## 9. Structured document ingestion (`tool_ingest_document`)

The route for anything that must be **stored exactly** and **linked**: requirements, legal text,
standards, specifications. It is also the integration surface for outside systems: a parser in
front of Codebeamer, Jira, EUR-Lex, a PDF pipeline and so on builds the JSON below. SMOS holds no
source-system-specific code.

```
tool_ingest_document(payload_json: str) -> dict
```

### 9.1 The payload (`IngestionPayload`, schema_version `"1.0"`)

```json
{
  "schema_version": "1.0",
  "external_handle": {
    "system": "cb-prod",
    "native_id": "SYS2-1042",
    "deep_link": "https://cb.example.com/cb/issue/1042",
    "version": "7",
    "source_updated_at": "2026-10-01T09:30:00Z"
  },
  "doc_name": "Collision braking",
  "doc_type": "requirement",
  "domain_tags": ["level:SYS.2", "domain:safety", "topic:braking"],
  "metadata": {"status": "Review", "priority": "High", "tracker": "21 System Requirements"},
  "blocks": [
    {"block_type": "heading",   "text": "Collision braking\n", "heading_level": 1},
    {"block_type": "paragraph", "text": "The brake controller shall apply full deceleration within 150 ms of a collision signal.\n"}
  ],
  "source_links": [
    {"target_native_id": "SYS1-10", "edge_type": "depends_on", "metadata": {}},
    {"target_native_id": "STD-26262-6", "edge_type": "cites", "metadata": {"clause": "9.4.2"}}
  ],
  "confidence": 1.0,
  "transformer_name": "codebeamer-export",
  "transformer_version": "0.3.0"
}
```

| Field | Required | Description |
|---|---|---|
| `schema_version` | no | Default `"1.0"`. |
| `external_handle.system` | **yes** | Namespace of the source (instance host, corpus name). Non-blank. |
| `external_handle.native_id` | **yes** | Item id inside that system. Non-blank. |
| `external_handle.deep_link`, `version`, `source_updated_at` | no | Provenance; stored with the payload. |
| `blocks` | **yes** | One or more content blocks, in order (see 9.2). |
| `doc_name` | no | Display name; defaults to the doc id. |
| `doc_type` | no | Free label. `legal` and `standard` make the document eligible for the `normative` section of the requirement context; persisted as segment tag `doctype:<value>`. |
| `domain_tags` | no | Tags copied onto every segment and the document node. **Must not contain `,`.** |
| `metadata` | no | Free JSON dict stored on the document node (`node.metadata.metadata`). |
| `source_links` | no | Typed links to other items (see 9.3). |
| `confidence` | no | 0–1, default 1.0. |
| `transformer_name`, `transformer_version` | no | Parser provenance. |

**Identity.** `doc_id = sha1("<system>:<native_id>")[:16]`, deterministic. The same
`(system, native_id)` always resolves to the same document, so ingestion is an **idempotent
upsert**.

### 9.2 Blocks

| `block_type` | Notes |
|---|---|
| `heading` | Requires `heading_level` 1–6. |
| `paragraph` | Plain text. |
| `list` | A run of list items. |
| `table` | A run of table rows. |

`text` must be non-blank. SMOS stores the **concatenation** of all block texts as the document
text, and `tool_reconstruct` returns exactly that, so include each block's own trailing
newline/whitespace in `text`. `source_anchor` (optional string) is an opaque pointer to where
the block came from in the source; SMOS carries it but does not interpret it.

### 9.3 Links (`source_links`)

Each link becomes a directed graph edge `this document --edge_type--> target`, **in the direction
you send it**.

| Field | Description |
|---|---|
| `target_native_id` | The target's `native_id`. |
| `edge_type` | Must be in the active vocabulary ([11.4](#114-edge-vocabulary)); otherwise the payload is quarantined. |
| `metadata` | Free dict stored on the edge. Reserved key `target_system` selects the target's namespace (default: this payload's `system`). Use `clause` on `cites` links for the cited clause. |

Rules and behaviour:

- A link to an item that is not ingested yet creates a **placeholder** document node
  (`metadata.placeholder = true`, name = `target_native_id`). Ingesting the real item later
  upgrades the node in place; links already pointing at it stay valid.
- Re-ingesting an item **replaces the links it owns** and leaves other items' links to it
  untouched.
- A link from an item to itself is rejected.
- Direction convention for traceability: for `depends_on`, the **dependent** item is the source
  and its **upstream** item is the target. An item's upstream = its outgoing `depends_on`; its
  downstream = its incoming `depends_on`.

### 9.4 Validation and quarantine

The payload is validated first. Violations are returned, and the payload is **parked, not
discarded**:

| Rule | Error text (prefix) |
|---|---|
| at least one block | `blocks: must contain at least one block` |
| non-blank block text | `blocks[i]: text must not be empty` |
| heading level 1–6 on headings | `blocks[i]: heading blocks require heading_level in 1..6` |
| non-blank `system` / `native_id` | `external_handle.system: ...` / `external_handle.native_id: ...` |
| known `edge_type` | `source_links[i]: unknown edge_type '...'` |
| no self-link | `source_links[i]: link targets its own item` |
| no comma in a tag | `domain_tags[i]: must not contain ','` |
| size | total block text ≤ 5,000,000 characters |

A payload that is not valid JSON, or does not match the schema types, returns
`{"status": "error", "error": "schema violation: ..."}` and is **not** quarantined.

### 9.5 Return values

Success:

```json
{
  "status": "success",
  "doc_id": "0b8e0c4d7a1f93aa",
  "segment_count": 2,
  "segment_ids": ["...", "..."],
  "section_count": 1,
  "reference_count": 0,
  "link_count": 2,
  "resumed": false
}
```

Quarantined:

```json
{"status": "quarantined", "quarantine_key": "f3a2...", "errors": ["source_links[0]: unknown edge_type 'foo'"]}
```

`resumed` is `true` when a previously interrupted ingest with identical content was continued.

### 9.6 What gets stored

1. **Document node** (`type: document`) with metadata `domain_tags`, `doc_type`, `system`,
   `native_id`, `metadata`.
2. **Sections and segments**: the text is split structurally (your block boundaries), then
   semantically, then by size. Every segment is a `segment` memory with exact text and the tags
   `domain_tags + ["doctype:<doc_type>"]`. Containment is `document --contains--> section
   --contains--> segment`.
3. **Link edges** from `source_links`, `origin: "source_link"`.
4. A **checkpoint** (per chunk of 64 segments) so an interrupted ingest resumes.

> **No domain adapter over MCP.** `tool_ingest_document` runs without a text adapter, so it
> extracts no cross-references from prose (`reference_count` is 0); relationships come from
> `source_links`. The Python entry point ([12](#12-python-api)) accepts an adapter
> (`MarkdownAdapter`, `LegalAdapter`) if you want in-text citation and term extraction.

### 9.7 Tag conventions used by retrieval

| Tag | Set by | Used for |
|---|---|---|
| `level:<L>` (e.g. `level:SYS.2`) | the parser | `level` filtering in `tool_requirement_context` |
| `topic:<name>` | the parser | grouping; counts as domain overlap for `peers` |
| `domain:<name>`, any free tag | the parser | `tool_semantic_query(tags=...)`, `peers` overlap, `normative_tags` |
| `doctype:<doc_type>` | SMOS | selects `legal` / `standard` segments for `normative` |

---

## 10. Requirement context (`tool_requirement_context`)

Gathers the evidence a model needs to judge whether a requirement is a good one — what it is
linked to, what looks like it, and which legal/standard clauses bear on it — as exact text with
reasons. **SMOS supplies evidence and plain coverage facts; it never returns a verdict.** No LLM
is called.

```
tool_requirement_context(text, doc_id, system, native_id, level, domain_tags,
                         normative_tags, declared_links_json, token_budget, k_per_section)
```

### 10.1 Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `text` | string | `""` | Draft requirement text. Optional if the item is stored; if both are given, `text` is used for similarity. |
| `doc_id` | string | `""` | Stored item's document id. |
| `system` + `native_id` | string | `""` | Alternative way to name a stored item. `system` also becomes the default namespace for `declared_links_json` targets. |
| `level` | string | `""` | Process level, e.g. `"SYS.2"`; peers must carry tag `level:SYS.2`. If empty, derived from a `level:` tag of the stored item. |
| `domain_tags` | string | `""` | Comma-separated. `peers` must share at least one **non-`level:`** tag. Empty = taken from the stored item. |
| `normative_tags` | string | `""` | Comma-separated. When set, `normative` is limited to legal/standard segments carrying at least one of these tags (e.g. `"iso26262"`). Empty = all legal/standard documents. |
| `declared_links_json` | string | `""` | JSON list of links a **draft** intends to have: `[{"target_native_id": "SYS1-10", "edge_type": "depends_on"}]`. Optional per link: `target_system`, `clause` (for `cites`). |
| `token_budget` | int | `4000` | Approximate token cap for all sections (`len(text)/4`), split evenly across the three sections. |
| `k_per_section` | int | `5` | Max items per section. |

One of `text`, `doc_id`, or `system`+`native_id` is required; otherwise `{"error": ...}`.
Naming a stored item that does not exist (or is only a placeholder) returns `{"error": ...}`.

### 10.2 Response

```json
{
  "query": {"doc_id": "…", "text": "…", "level": "SYS.2", "domain_tags": ["domain:safety"]},
  "linked":    [ ITEM, … ],
  "peers":     [ ITEM, … ],
  "normative": [ ITEM, … ],
  "coverage": {
    "has_upstream": true,
    "has_downstream": true,
    "near_duplicates": 1,
    "normative_by_source": {"legal": 0, "standard": 1},
    "unresolved_links": ["SYS1-77"]
  },
  "truncated": {"linked": false, "peers": false, "normative": true}
}
```

**Item fields**

| Field | Sections | Description |
|---|---|---|
| `id`, `doc_id` | all | Document id of the item. |
| `doc_name` | all | Display name. |
| `text` | all | Exact stored text. For `linked`, the whole item; for `peers`/`normative`, the best-matching segment. |
| `score` | peers, normative | Cosine similarity of the matching segment, or `null` when it came only from BM25. |
| `reason` | all | Why it is here (table below). |
| `hops`, `path` | linked | Distance (1–2) and the edges walked: `[{"edge_type", "direction": "out"\|"in"\|"declared", "node"}]`. |
| `segment_id` | peers, normative | The matching segment's memory id. |
| `near_duplicate` | peers | `true` when similarity ≥ 0.90 (a tunable default, not a calibrated threshold). |
| `clause` | normative | The cited clause for explicit citations. |
| `source_kind` | normative | `legal` or `standard` for semantic matches. |
| `clipped` | all | `true` when the text was cut to fit the budget. |

**`reason` values**

| Section | Reason | Meaning |
|---|---|---|
| linked | `upstream (depends_on)` | the item depends on this one (outgoing `depends_on`) |
| linked | `downstream (depends_on)` | this one depends on the item (incoming `depends_on`) |
| linked | `parent (parent_of)` / `child (parent_of)` | hierarchy |
| linked | `member of (member_of)` / `has member (member_of)` | epic / group membership |
| linked | `declared <edge_type>` | link declared in `declared_links_json` |
| peers | `similar requirement` / `near duplicate` | semantic + lexical match at the same level/domain |
| normative | `explicit citation` | reached through a `cites` link (stored, or declared) |
| normative | `legal match` / `standard match` | semantic match |

### 10.3 Behaviour

- **`linked`** walks `depends_on`, `parent_of`, `member_of` in both directions up to **2 hops**
  from the stored item (or from each declared target). Placeholders (targets not yet ingested)
  are not listed; they appear in `coverage.unresolved_links`. Cycles terminate.
- **`peers`** are the best segment per other requirement-like document that shares the level
  and a domain tag; normative documents and items already in `linked` are excluded.
- **`normative`** lists explicitly cited documents first, then semantic matches among
  `doctype:legal` / `doctype:standard` segments.
- **Budget.** Each section gets `token_budget / 3`. Items are kept in order; the first item is
  clipped (`clipped: true`) rather than dropped if it alone exceeds the budget, and
  `truncated.<section>` reports that something was cut.
- **Coverage** is facts, not judgement: `has_upstream` / `has_downstream` (links exist, even to
  placeholders), `near_duplicates`, `normative_by_source` (counts per `legal`/`standard`; a `0`
  is a real "nothing matched"), `unresolved_links`.

### 10.4 Example

```
tool_requirement_context(
  text="The brake controller shall apply full deceleration within 180 ms of a collision signal.",
  system="cb-prod",
  level="SYS.2",
  domain_tags="domain:safety",
  normative_tags="iso26262",
  declared_links_json='[{"target_native_id":"SYS1-10","edge_type":"depends_on"}]'
)
```

---

## 11. Data model

### 11.1 Memory types

`doc`, `adr`, `log`, `issue` (via `tool_semantic_store`/`tool_semantic_write`), `episodic`
(`tool_remember`), `semantic` (`tool_learn`), `compressed` (`tool_compress`), `reflection`
(`tool_reflect`), `segment` (ingested documents).

### 11.2 Tiers

`hot` / `warm` / `cold`. Recomputed from recall history: `hot` = recalled within 7 days or more
than 10 times, `warm` = recalled within 30 days or more than 3 times, otherwise `cold`. Tiers are
informational; they do not currently filter search.

### 11.3 Graph nodes

| Type | Created by | Id form |
|---|---|---|
| `memory` | every stored memory | the memory UUID |
| `entity` | code/backtick entity extraction | `entity:<name>` |
| `concept` | document reference extraction | `concept:<name>`; document-local targets (e.g. a legal "Article 3"): `concept:<doc_id>:<name>` |
| `document` | `tool_ingest_document`, link placeholders | `derive_doc_id(system, native_id)` |
| `section` | document segmentation | `<doc_id>:section:<n>` |
| `repo_component`, `snapshot`, `agent` | repository snapshot and agent primitives | — |

### 11.4 Edge vocabulary

Core (always valid): `references`, `depends_on`, `related_to`, `implements`, `contains`,
`evolves_into`, `generated_from`.

Added by registered adapters (the markdown and requirements adapters register at import):
`parent_of`, `member_of`, `cites` (requirements). Meanings:

| Edge | Direction | Meaning |
|---|---|---|
| `contains` | parent → child | document structure only (document → section → segment). Not for item hierarchy. |
| `references` | referrer → referenced | a link or citation |
| `depends_on` | dependent → upstream | traceability |
| `parent_of` | parent item → child item | requirement tree |
| `member_of` | item → epic/group | grouping |
| `cites` | item → normative document | citation; clause in edge metadata |
| `generated_from` | derived → source | produced by `tool_compress` |
| `evolves_into`, `related_to`, `implements` | — | free semantic links (`tool_link`) |

---

## 12. Python API

The MCP tools are thin wrappers; the same functions can be called directly (scripts, tests,
other services) against a `VectorStore`.

```python
from pathlib import Path
from smos.memory.vector_store import VectorStore

store = VectorStore(persist_path=Path(".smos"))
```

| Function | Module | Purpose |
|---|---|---|
| `do_ingest_document_schema(payload, store, adapter=None) -> dict` | `smos.tools.ingest_tools` | Ingest an `IngestionPayload`. `adapter` is an optional `DomainAdapter` for in-text reference extraction. |
| `requirement_context(store, *, text, doc_id, system, native_id, level, domain_tags, normative_tags, declared_links, token_budget, k_per_section) -> dict` | `smos.memory.requirement_context` | Typed-argument form of `tool_requirement_context` (`domain_tags`/`normative_tags` are lists, `declared_links` is a list of dicts). |
| `requirement_context_from_strings(store, ...) -> dict` | `smos.memory.requirement_context` | The string-argument form the MCP tool uses. |
| `semantic_query(query, k, store, tags=None)` | `smos.tools.semantic_tools` | Hybrid compressed retrieval. |
| `recall(query, strategy, k, since, until, seed_id, component, store)` | `smos.memory.recall_engine` | Strategy dispatch. |
| `reconstruct(store, node_id)` | `smos.memory.exact_retrieval` | Rebuild exact text. |
| `register_adapter(adapter)` | `smos.ingest.adapters.registry` | Add a domain adapter (and its edge types) to the vocabulary. |
| `markdown_to_payload(text, system, native_id, doc_name=None)` | `smos.ingest.transformers.markdown_transformer` | Reference client: markdown → `IngestionPayload`. |

Registering an adapter before ingesting changes which `edge_type` values validate:

```python
from smos.ingest.adapters.registry import register_adapter
from smos.ingest.adapters.legal import LegalAdapter   # not registered by default

register_adapter(LegalAdapter())
```

Building a payload in code:

```python
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, SourceLink
from smos.tools.ingest_tools import do_ingest_document_schema

payload = IngestionPayload(
    external_handle=ExternalHandle(system="cb-prod", native_id="SYS2-1042"),
    doc_name="Collision braking",
    doc_type="requirement",
    domain_tags=["level:SYS.2", "domain:safety"],
    blocks=[ContentBlock(block_type="paragraph", text="The brake controller shall ...\n")],
    source_links=[SourceLink(target_native_id="SYS1-10", edge_type="depends_on")],
)
result = do_ingest_document_schema(payload, store)
```

---

## 13. End-to-end recipes

### 13.1 Requirements traceability pipeline

1. A parser exports each item as an `IngestionPayload` (section 9) — level in `domain_tags`
   (`level:SYS.1` …), association → `source_links` with `depends_on`, epics → `member_of`,
   standard citations → `cites` with `metadata.clause`.
2. Ingest normative sources the same way with `doc_type: "legal"` or `"standard"` and
   `domain_tags` such as `["iso26262"]`.
3. Ingest items in any order — forward links resolve when the target arrives.
4. For a stored item:
   `tool_requirement_context(system="cb-prod", native_id="SYS2-1042")`.
5. For a draft, add `declared_links_json` for the links it will have; check
   `coverage.has_upstream`, `near_duplicates` and `normative_by_source` before reading the text
   sections.
6. After an edit in the source system, re-send the item; its text, tags and owned links are
   replaced and everyone else's links to it survive.

### 13.2 Exact recall of a stored document

```
tool_recall(query="stopping distance obligation", strategy="exact", k=3)   # → segments + neighbours
tool_reconstruct(node_id="<doc_id from metadata.doc_id>")                  # → whole document text
```

### 13.3 Code / prose memory for an agent

```
tool_semantic_query("how does the auth flow work")        # before reading any file
tool_read_file_compress("/abs/path/auth.py")              # file not about to be edited
tool_store_verbatim(content=diff, label="auth-refactor")  # exact bytes, get a key
tool_semantic_store("Decided to replace JWT with sessions — see key <key>")
```

---

## 14. Limits and gotchas

- **Minimum input** is 10 non-blank characters for free-text tools.
- **Document size** cap is 5,000,000 characters of block text per payload. LLM summarisation is
  practical up to roughly 50 KB per file (model context).
- **Tags** are stored comma-joined: never put `,` in a tag (such payloads are quarantined).
- **Same-system default:** `source_links` and `declared_links_json` targets live in the item's
  own `system` unless `target_system` is given. A link into another system's namespace without
  `target_system` points at a placeholder.
- **Cross-document citations are not resolved.** `Article 3` inside one document never links to
  another document's Article 3 (they are deliberately separate nodes); links between documents
  must come from `source_links`.
- **Candidate pool.** `peers` and `normative` are drawn from the top
  `max(k_per_section × 100, 1000)` hybrid candidates. A corpus with far more requirement segments
  than that can push a relevant standard clause out; `normative_by_source` may then show `0` for a
  clause that exists.
- **Near-duplicate threshold** (cosine ≥ 0.90) is a default, not calibrated on your corpus.
- **Single writer.** One server process owns `.smos/`; embedding is done under the store lock, so
  concurrent heavy writes serialise.
- **Quarantine** keeps the rejected payload and its errors; fix and re-send the payload (same
  `system` + `native_id`) — it is not retried automatically.
- **Recall side effects:** retrieval tools bump recall counters on the returned memories (this
  drives tier promotion).
- **Data location:** `<project>/.smos/` (`faiss.index`, `metadata.db`, `workspace/`, `logs/`).
  Deleting it deletes the memory. SQLite is authoritative; the FAISS index is rebuilt from it if
  they diverge.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Summarisation model |
| `SUMMARIZER_MAX_TOKENS` | `512` | Max tokens per summary |

## CLI

```
smos setup [--skip-claude-md] [--dry-run]   # install deps, pull model, register MCP, write policy
smos update [--dry-run]                      # upgrade (editable reinstall from a local checkout)
smos uninstall [--dry-run]                   # remove MCP registration and policy block
smos --version
smos-server                                  # run the MCP server directly (stdio)
```
