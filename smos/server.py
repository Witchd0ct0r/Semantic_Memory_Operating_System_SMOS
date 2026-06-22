from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore
from smos.tools.file_tools import read_file_compress, write_file_safe
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
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
              When omitted, auto-detects domain from query keywords.
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


def run() -> None:
    app.run()


if __name__ == "__main__":
    run()
