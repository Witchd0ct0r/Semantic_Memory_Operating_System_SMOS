"""Callable equivalents of the SMOS MCP tools, bound to the isolated sandbox
store, for use without a running MCP server -- same pattern as
sandbox/toolchain-benchmark/scripts/smos_tools.py (2026-07-27 toolchain
benchmark), reused here so this benchmark's "direct Python calls" SMOS run
uses an identical mechanism to that one's, making the two runs' token counts
actually comparable (unlike the original smos_result.json in this benchmark,
which used a real MCP-tool multi-turn conversation -- see the "Mechanism
caveat" section of docs/eu-law-benchmark-results.md for why that matters).

Each function here mirrors one MCP tool 1:1 (see smos/server.py):

  tool_semantic_query(query, k=5, tags="")  -> semantic_query(query, k, store, tags)
  tool_recall(query, strategy="hybrid", ...) -> recall(query, strategy, k, since, until, seed_id, component, store)
  tool_reconstruct(node_id)                  -> reconstruct(store, node_id)

Usage: python smos_tools.py semantic_query "some query" [k] [tags]
       python smos_tools.py recall "some query" [strategy] [k]
       python smos_tools.py reconstruct <node_id>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SANDBOX_DIR = _SCRIPT_DIR.parent
_STORE_DIR = _SANDBOX_DIR / ".smos"
_REPO_ROOT = _SANDBOX_DIR.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.memory.exact_retrieval import reconstruct as _reconstruct
from smos.memory.recall_engine import recall as _recall
from smos.memory.vector_store import VectorStore
from smos.tools.semantic_tools import semantic_query as _semantic_query

_store = VectorStore(persist_path=_STORE_DIR)


def cmd_semantic_query(query: str, k: int = 5, tags: str = "") -> dict:
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return _semantic_query(query, k, _store, tags=tag_list)


def cmd_recall(query: str, strategy: str = "hybrid", k: int = 5) -> dict:
    return _recall(query, strategy, k, "", "", "", "", _store)


def cmd_reconstruct(node_id: str) -> dict:
    return _reconstruct(_store, node_id)


if __name__ == "__main__":
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "semantic_query":
        result = cmd_semantic_query(args[0], int(args[1]) if len(args) > 1 else 5,
                                     args[2] if len(args) > 2 else "")
    elif cmd == "recall":
        result = cmd_recall(args[0], args[1] if len(args) > 1 else "hybrid",
                             int(args[2]) if len(args) > 2 else 5)
    elif cmd == "reconstruct":
        result = cmd_reconstruct(args[0])
    else:
        raise SystemExit(f"unknown command: {cmd}")
    print(json.dumps(result, indent=2, default=str))
