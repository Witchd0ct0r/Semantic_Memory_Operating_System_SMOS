"""Ingest this repo's own code into SMOS via the direct-function-call
workaround (no MCP server registered on this machine yet -- see
docs/runbooks/environment.md). Restricted to code extensions only; markdown
docs are handled separately by .claude/skills/smos-doc-sync/.

Usage: python docs/runbooks/scripts/ingest_code.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_recursive_semantic_ingest

CODE_INCLUDE_PATTERNS = [
    "*.py", "*.js", "*.ts", "*.tsx", "*.jsx",
    "*.java", "*.go", "*.rs", "*.c", "*.cpp", "*.h", "*.hpp",
]

if __name__ == "__main__":
    store = VectorStore(persist_path=_REPO_ROOT / ".smos")
    result = do_recursive_semantic_ingest(
        path=str(_REPO_ROOT),
        store=store,
        include_patterns=CODE_INCLUDE_PATTERNS,
        summarize=True,
    )
    print(json.dumps(result, indent=2, default=str))
