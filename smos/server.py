from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

from smos.memory.lifecycle import LifecycleManager
from smos.memory.recall_engine import recall
from smos.memory.exact_retrieval import reconstruct
from smos.memory.requirement_context import requirement_context_from_strings
from smos.memory.vector_store import VectorStore
from smos.tools.agent_primitives import (
    compress,
    forget,
    learn,
    link,
    reflect,
    remember,
    snapshot,
)
from smos.tools.file_tools import read_file_compress, write_file_safe
from smos.tools.ingest_tools import (
    do_recursive_semantic_ingest,
    do_bulk_read,
    do_semantic_snapshot_repo,
    do_ingest_document_schema,
)
from pydantic import ValidationError

from smos.ingest.contract import IngestionPayload
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
    graph_query,
    semantic_query,
    semantic_store,
    semantic_write,
)

app = FastMCP(
    "smos",
    instructions=(
        "SMOS — Semantic Memory Operating System.\n\n"
        "FILE READING POLICY:\n"
        "- Use tool_read_file_compress for ANY file you are NOT about to edit. "
        "Pass the absolute path. The full file content will NOT enter the context window — "
        "only a compressed summary is returned and stored.\n"
        "- Use the built-in Read tool ONLY immediately before an Edit or Write call.\n"
        "- Before reading any file, call tool_semantic_query first. "
        "If the query returns useful context, skip the read entirely.\n\n"
        "STORAGE POLICY:\n"
        "- Prose, analysis, notes → tool_semantic_store (LLM-compressed, queryable by meaning).\n"
        "- Code, diffs, structured data, exact text → tool_store_verbatim (lossless, retrieve by key).\n\n"
        "SYNTHESIS:\n"
        "Use tool_semantic_query to retrieve compressed context. "
        "Do NOT re-read files already compressed — query memory instead."
    ),
)

# Each project gets its own .smos/ directory. The MCP server is started
# as a subprocess by Claude Code from the project root, so cwd == project root.
_PROJECT_ROOT = Path.cwd()
_DATA_DIR = _PROJECT_ROOT / ".smos"

_store = VectorStore(persist_path=_DATA_DIR)
_lifecycle = LifecycleManager(_store)
_store._lifecycle_callback = _lifecycle.on_insert


def _check_input(text: str, field: str = "text") -> dict | None:
    if not text or len(text.strip()) < _MIN_INPUT_CHARS:
        return {**_INVALID_INPUT_RESPONSE, "field": field}
    return None


@app.tool()
def tool_semantic_store(text: str, type: str = "doc") -> str:
    """Store text as semantic memory and return its ID.

    Args:
        text: The text content to store.
        type: Memory type — one of: doc, adr, log, issue.
    """
    try:
        if err := _check_input(text):
            return str(err)
        return semantic_store(text, type, _store)
    except Exception as exc:
        return str({"error": f"{type(exc).__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_semantic_query(query: str, k: int = 5, tags: str = "") -> dict:
    """Retrieve a compressed context summary relevant to the query.

    Returns a dict with keys: summary (str), sources (list[str]), confidence (float 0-1),
    mode (abstractive | extractive | uncertain).

    At large scale (many domains), pass tags to scope the search to specific domains
    and avoid retrieving unrelated memories. Example: tags="auth,security"

    Args:
        query: Natural language query.
        k: Number of results to return (default 5 — rarely need more than 10).
        tags: Optional comma-separated domain tags to restrict the search scope.
              Example: "auth,security" or "database,performance".
              When omitted, the search is unscoped.
    """
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        return semantic_query(query, k, _store, tags=tag_list)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_semantic_write(type: str, content: str, tags: str = "") -> str:
    """Write a structured memory object and return its ID.

    Args:
        type: Memory type — one of: doc, adr, log, issue.
        content: The text content to store.
        tags: Optional comma-separated tag list (e.g. "database,performance").
    """
    try:
        if err := _check_input(content, "content"):
            return str(err)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        return semantic_write(type, content, _store, tags=tag_list)
    except Exception as exc:
        return str({"error": f"{type(exc).__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_graph_query(term: str, max_hops: int = 2) -> dict:
    """Traverse the memory graph starting from an entity or memory id.

    Finds nodes connected to `term` within `max_hops` — e.g. term="JWT" finds
    every memory that references JWT (1 hop) and other entities those
    memories also reference (2 hops), transitively. If `term` isn't an
    existing node id, it's resolved as `entity:{term}` automatically.

    Returns {"term", "start_id", "nodes": [{"id","type","name","metadata","hop"}], "count"}.

    Args:
        term: Entity name (e.g. "JWT") or an exact node id.
        max_hops: Traversal depth (default 2).
    """
    try:
        return graph_query(term, _store, max_hops=max_hops)
    except Exception as exc:
        return {
            "term": term, "start_id": None, "nodes": [], "count": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


@app.tool()
def tool_remember(content: str, type: str = "episodic") -> str:
    """Store an episodic memory and return its ID.

    Args:
        content: The text content to remember.
        type: Memory type (default "episodic").
    """
    try:
        if err := _check_input(content, "content"):
            return str(err)
        return remember(content, type, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_forget(memory_id: str) -> dict:
    """Delete a memory and its graph edges.

    Args:
        memory_id: The memory's id to delete.
    """
    try:
        ok = forget(memory_id, _store)
        return {"success": ok, "error": None}
    except Exception as exc:
        return {"success": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_learn(content: str) -> str:
    """Store a semantic (high-confidence) memory and return its ID.

    Args:
        content: The text content to learn.
    """
    try:
        if err := _check_input(content, "content"):
            return str(err)
        return learn(content, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_link(id_a: str, id_b: str, edge_type: str) -> dict:
    """Create an explicit graph edge between two memories.

    Args:
        id_a: Source memory id.
        id_b: Target memory id.
        edge_type: One of references, depends_on, related_to, implements,
                   contains, evolves_into, generated_from.
    """
    try:
        return link(id_a, id_b, edge_type, _store)
    except Exception as exc:
        return {"success": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_compress(ids: str) -> str:
    """Merge multiple memories into one compressed memory, preserving source links.

    Sources are kept, not deleted — the new memory links back to them via
    source_ids and generated_from graph edges.

    Args:
        ids: Comma-separated memory ids to merge.
    """
    try:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        return compress(id_list, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_reflect(query: str, k: int = 5) -> str:
    """Reason about what the agent knows relevant to query, store the reflection, return its ID.

    Args:
        query: Natural language question about the agent's own knowledge.
        k: Number of retrieved memories to reflect over (default 5).
    """
    try:
        if err := _check_input(query, "query"):
            return str(err)
        return reflect(query, k, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_snapshot() -> dict:
    """Take a repository snapshot of the current project root (no path argument needed)."""
    try:
        return snapshot(str(_PROJECT_ROOT), _store)
    except Exception as exc:
        return {"status": "error", "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}


@app.tool()
def tool_recall(
    query: str,
    strategy: str = "hybrid",
    k: int = 5,
    since: str = "",
    until: str = "",
    seed_id: str = "",
    component: str = "",
) -> dict:
    """Retrieve memories using an explicit strategy: hybrid, temporal, chain, repo, or exact.

    hybrid (default): the existing vector+BM25+graph retrieval pipeline.
    temporal: filter by [since, until] ISO-8601 timestamps, then rank by similarity.
    chain: follow evolves_into/related_to edges from seed_id; k is hop depth here, not result count.
    repo: hybrid retrieval scoped to a single component tag.
    exact: verbatim segments (no LLM compression) plus +/-1 ordinal neighbor context —
           Stage 2 of the two-stage retrieval loop. Pair with tool_reconstruct to rebuild
           the full surrounding document or section from the matched segments.

    Args:
        query: Natural language query (unused for the chain strategy).
        strategy: One of hybrid, temporal, chain, repo, exact (default "hybrid").
        k: Result count for hybrid/temporal/repo/exact, or hop depth for chain (default 5).
        since: ISO-8601 lower timestamp bound for temporal (empty = unbounded).
        until: ISO-8601 upper timestamp bound for temporal (empty = unbounded).
        seed_id: Starting memory id for the chain strategy.
        component: Tag to scope the repo strategy to.
    """
    try:
        return recall(query, strategy, k, since, until, seed_id, component, _store)
    except Exception as exc:
        return {"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_reconstruct(node_id: str) -> dict:
    """Reconstruct the verbatim text of a document or section from its stored segments.

    Fetches every segment under `node_id` (a document or section graph node id —
    e.g. the "doc_id" returned by tool_ingest_document, or a section id such as
    "<doc_id>:section:0"), orders them by ordinal, and concatenates their exact
    stored text. Pair with tool_recall(strategy="exact") for the two-stage
    retrieval loop: exact finds the matching segments, reconstruct rebuilds
    their surrounding document.

    Returns {"node_id", "text", "segment_count", "complete", "error"}.
    `complete` is False when the segments have a gap (e.g. an in-progress
    ingest, or char offsets aren't contiguous) — the text may not be fully
    byte-exact in that case.

    Args:
        node_id: A document or section graph node id.
    """
    try:
        return reconstruct(_store, node_id)
    except Exception as exc:
        return {
            "node_id": node_id, "text": "", "segment_count": 0,
            "complete": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


@app.tool()
def tool_requirement_context(
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: str = "",
    normative_tags: str = "",
    declared_links_json: str = "",
    token_budget: int = 4000,
    k_per_section: int = 5,
) -> dict:
    """Gather evidence for judging a requirement: linked items, similar items,
    relevant legal/standard clauses, and coverage facts. Exact stored text, no
    LLM compression, no verdict.

    Give a draft `text`, a stored item (`doc_id`, or `system` + `native_id`), or
    both (the given text then overrides the stored text for similarity).

    Returns {"query", "linked", "peers", "normative", "coverage", "truncated"}:
      linked     items connected by depends_on/parent_of/member_of (<= 2 hops),
                 each with reason ("upstream (depends_on)", ...) and path
      peers      similar items at the same level/domain; near_duplicate flagged
      normative  legal/standard segments; explicitly cited ones come first
      coverage   has_upstream, has_downstream, near_duplicates,
                 normative_by_source, unresolved_links

    Args:
        text: Draft requirement text (optional if the item is stored).
        doc_id: Stored item's document id (alternative to system + native_id).
        system: Source system of the item; also the default namespace for
                declared link targets.
        native_id: Item id in the source system.
        level: Process level, e.g. "SYS.2" (matches tag "level:SYS.2").
        domain_tags: Comma-separated tags; peers must share at least one.
        normative_tags: Comma-separated tags scoping the normative section
                        (e.g. "iso26262"); empty = all legal/standard documents.
        declared_links_json: JSON list of links a draft intends to have, e.g.
                        [{"target_native_id": "1234", "edge_type": "depends_on"}].
        token_budget: Approximate token cap across all sections (default 4000).
        k_per_section: Max items per section (default 5).
    """
    try:
        return requirement_context_from_strings(
            _store,
            text=text,
            doc_id=doc_id,
            system=system,
            native_id=native_id,
            level=level,
            domain_tags=domain_tags,
            normative_tags=normative_tags,
            declared_links_json=declared_links_json,
            token_budget=token_budget,
            k_per_section=k_per_section,
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_read_file_compress(path: str) -> dict:
    """Read a file, compress it with the local LLM, store the summary, and return it.

    Use this instead of the built-in Read tool for any file you are NOT about to edit.
    Accepts absolute paths (e.g. C:/project/src/foo.py) or relative paths inside the workspace.
    Returns dict with keys: summary, id, source, error.

    Args:
        path: Absolute or workspace-relative file path.
    """
    try:
        return read_file_compress(path, _store)
    except Exception as exc:
        return {
            "summary": None,
            "id": None,
            "source": path,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


@app.tool()
def tool_store_verbatim(content: str, label: str = "") -> dict:
    """Store content losslessly (no LLM summarization) and return a retrieval key.

    Use for code, diffs, structured data, or any artifact where exact bytes matter.
    The full content is kept out of the context window; only the key is returned.
    Retrieve later with tool_retrieve(key).

    Args:
        content: Exact content to store.
        label: Short human-readable label, e.g. "refactored vector_store.py diff".
    """
    try:
        if not content or not content.strip():
            return {"key": None, "label": label, "bytes": 0, "error": "content is empty"}
        key = _store.store_verbatim(content, label=label)
        return {"key": key, "label": label, "bytes": len(content.encode("utf-8")), "error": None}
    except Exception as exc:
        return {"key": None, "label": label, "bytes": 0, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_retrieve(key: str) -> dict:
    """Retrieve verbatim content previously stored with tool_store_verbatim.

    Args:
        key: The key returned by tool_store_verbatim.
    """
    try:
        result = _store.retrieve_verbatim(key)
        if result is None:
            return {"key": key, "content": None, "label": None, "error": "not found"}
        return {**result, "error": None}
    except Exception as exc:
        return {"key": key, "content": None, "label": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_write_file_safe(path: str, content: str) -> dict:
    """Write content to a file inside the sandboxed workspace directory.

    Returns {"success": true} on success. Path traversal attempts are blocked.

    Args:
        path: Relative path inside the workspace directory.
        content: File content to write.
    """
    try:
        return write_file_safe(path, content)
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_recursive_semantic_ingest(
    path: str,
    recursive: bool = True,
    include_patterns: str = "",
    exclude_patterns: str = "",
    max_files: int = 5000,
    summarize: bool = True,
    store_raw_metadata: bool = True,
) -> dict:
    """Recursively scan a directory and ingest every supported text file into semantic memory.

    Supported extensions: .py .js .ts .tsx .jsx .java .go .rs .c .cpp .h .hpp
                          .md .txt .rst .yaml .yml .json .toml .ini .cfg

    Automatically skips: .git node_modules venv __pycache__ build dist target

    Returns a structured ingestion report with file counts and timing.

    Args:
        path: Absolute path to the directory to ingest.
        recursive: Walk subdirectories (default True).
        include_patterns: Comma-separated glob patterns — only matching files are ingested.
                          Example: "*.py,*.md"  (empty = all supported files)
        exclude_patterns: Comma-separated glob patterns — matching files are skipped.
                          Example: "test_*.py,*_generated.*"
        max_files: Hard cap on files to process in one call (default 5000).
        summarize: True = use local LLM to compress each file (slower, better quality).
                   False = store the first 500 chars verbatim (fast, good for large repos).
        store_raw_metadata: Tag each memory with file size and extension (default True).
    """
    inc = [p.strip() for p in include_patterns.split(",") if p.strip()] or None
    exc = [p.strip() for p in exclude_patterns.split(",") if p.strip()] or None
    try:
        return do_recursive_semantic_ingest(
            path, _store,
            recursive=recursive,
            include_patterns=inc,
            exclude_patterns=exc,
            max_files=max_files,
            summarize=summarize,
            store_raw_metadata=store_raw_metadata,
        )
    except Exception as exc_:
        return {"status": "error", "error": f"{type(exc_).__name__}: {str(exc_)[:300]}"}


@app.tool()
def tool_bulk_read(paths: str) -> dict:
    """Read multiple files in parallel and return their contents in order.

    Significantly faster than N sequential tool_read_file_compress calls for
    pure content retrieval (no compression or storage).

    Args:
        paths: Comma-separated list of absolute file paths to read.
               Example: "/repo/src/a.py,/repo/src/b.py,/repo/README.md"
    """
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    try:
        return do_bulk_read(path_list)
    except Exception as exc_:
        return {"status": "error", "error": f"{type(exc_).__name__}: {str(exc_)[:300]}"}


@app.tool()
def tool_ingest_document(payload_json: str) -> dict:
    """Ingest a structure-preserving document via the versioned ingestion contract.

    Non-conforming payloads are quarantined (not rejected) with validation
    errors returned so the client can inspect and resubmit. Re-ingesting the
    same external_handle (system + native_id) is an idempotent upsert: it
    replaces a prior completed ingest, or resumes a prior interrupted one if
    the content is unchanged.

    Args:
        payload_json: JSON string matching smos.ingest.contract.IngestionPayload
                      (schema_version, external_handle{system,native_id,...},
                      blocks[{block_type,text,heading_level?,source_anchor?}], ...).
    """
    try:
        payload = IngestionPayload.model_validate_json(payload_json)
    except ValidationError as exc:
        return {"status": "error", "error": f"schema violation: {str(exc)[:500]}"}
    try:
        return do_ingest_document_schema(payload, _store)
    except Exception as exc_:
        return {"status": "error", "error": f"{type(exc_).__name__}: {str(exc_)[:300]}"}


@app.tool()
def tool_semantic_snapshot_repo(path: str) -> dict:
    """Generate a comprehensive semantic snapshot of an entire repository.

    Pipeline:
      1. Scan all files → language breakdown, important-file list
      2. Parse dependency manifests (requirements.txt, package.json, etc.)
      3. Extract import graph via lightweight regex (Python + JS/TS)
      4. Ingest all files into semantic memory (fast mode)
      5. Generate architecture summary via local LLM
      6. Store snapshot as a queryable memory entry

    The snapshot memory is retrievable later with:
        tool_semantic_query(query="repository architecture", tags="snapshot")

    Args:
        path: Absolute path to the repository root.
    """
    try:
        return do_semantic_snapshot_repo(path, _store)
    except Exception as exc_:
        return {"status": "error", "error": f"{type(exc_).__name__}: {str(exc_)[:300]}"}


def run() -> None:
    app.run()


if __name__ == "__main__":
    run()
