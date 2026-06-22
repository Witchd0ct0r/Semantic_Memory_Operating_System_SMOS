from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from smos.llm.summarizer import summarize_text
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

_WORKSPACE = Path.cwd() / ".smos" / "workspace"
_LOGS = Path.cwd() / ".smos" / "logs"
_LOG_LOCK = threading.Lock()


def _resolve_safe_path(relative_path: str) -> Path:
    if "\x00" in relative_path:
        raise PermissionError("Null byte in path rejected.")
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    target = (_WORKSPACE / relative_path).resolve()
    try:
        target.relative_to(_WORKSPACE.resolve())
    except ValueError:
        raise PermissionError("Path traversal attempt blocked.")
    return target


def _log_write(relative_path: str, byte_count: int) -> None:
    _LOGS.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "path": relative_path,
        "bytes": byte_count,
    })
    with _LOG_LOCK:
        with open(_LOGS / "writes.jsonl", "a", encoding="utf-8") as f:
            f.write(entry + "\n")


def read_file_compress(path: str, store: VectorStore) -> dict:
    p = Path(path)
    if p.is_absolute():
        target = p
    else:
        try:
            target = _resolve_safe_path(path)
        except PermissionError as exc:
            return {"summary": None, "id": None, "source": path, "error": str(exc)}

    if not target.exists():
        return {"summary": None, "id": None, "source": path, "error": "File not found."}

    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"summary": None, "id": None, "source": path, "error": str(exc)}

    summary = summarize_text(content, context_hint=f"File: {path}")

    memory = MemoryObject(type="doc", content=summary, tags=["file", path])
    memory_id = store.store(memory)

    return {"summary": summary, "id": memory_id, "source": path, "error": None}


def write_file_safe(path: str, content: str) -> dict:
    try:
        safe_path = _resolve_safe_path(path)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        _log_write(path, len(content.encode("utf-8")))
        return {"success": True}
    except (PermissionError, OSError, ValueError):
        return {"success": False}
