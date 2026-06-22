"""
MCP Tool Correctness Benchmark
QA test suite for semantic memory tools — Groups 1-5, 40 tests total.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, r'C:\Private\semantic_memory')

from memory.vector_store import VectorStore
from memory.schemas import MemoryObject
from tools.semantic_tools import semantic_store, semantic_query, semantic_write
import tools.file_tools as ft
from datetime import datetime

# ---------------------------------------------------------------------------
# Group 1 — semantic_store()  [10 tests]
# ---------------------------------------------------------------------------

data_dir = Path(tempfile.mkdtemp())
store = VectorStore(persist_path=data_dir)
results_g1: dict[str, bool] = {}

# T1: basic doc
r = semantic_store("Redis is an in-memory key-value store.", "doc", store)
results_g1["T1_returns_string"] = isinstance(r, str) and len(r) > 0

# T2: valid UUID
results_g1["T2_valid_uuid"] = bool(re.match(r'^[0-9a-f-]{36}$', r))

# T3: type adr
r2 = semantic_store("Use PostgreSQL for primary storage.", "adr", store)
results_g1["T3_adr_type"] = isinstance(r2, str)

# T4: type log
r3 = semantic_store("ERROR: connection timeout at 14:32:01", "log", store)
results_g1["T4_log_type"] = isinstance(r3, str)

# T5: type issue
r4 = semantic_store("Issue: memory leak in worker process", "issue", store)
results_g1["T5_issue_type"] = isinstance(r4, str)

# T6: empty string — should not crash
try:
    r5 = semantic_store("", "doc", store)
    results_g1["T6_empty_no_crash"] = isinstance(r5, str)
except Exception:
    results_g1["T6_empty_no_crash"] = False

# T7: 5000-char text
r6 = semantic_store("PostgreSQL " * 500, "doc", store)
results_g1["T7_long_text"] = isinstance(r6, str)

# T8: unicode
r7 = semantic_store("Система управления базами данных 🚀", "doc", store)
results_g1["T8_unicode"] = isinstance(r7, str)

# T9: two stores of same text → different IDs
r8 = semantic_store("Identical text", "doc", store)
r9 = semantic_store("Identical text", "doc", store)
results_g1["T9_different_ids"] = r8 != r9

# T10: count increments (empty-string inputs are now gated, so 8 docs stored by now)
results_g1["T10_count_correct"] = store.count() >= 8

# ---------------------------------------------------------------------------
# Group 2 — semantic_write()  [6 tests]
# ---------------------------------------------------------------------------

results_g2: dict[str, bool] = {}

r = semantic_write("adr", "Use PostgreSQL.", store, tags=["database", "adr"])
results_g2["T1_returns_str"] = isinstance(r, str)
results_g2["T2_valid_uuid"] = bool(re.match(r'^[0-9a-f-]{36}$', r))

# No tags
r2 = semantic_write("log", "Deployment done.", store, tags=None)
results_g2["T3_no_tags_no_crash"] = isinstance(r2, str)

# Empty tags list
r3 = semantic_write("doc", "Note.", store, tags=[])
results_g2["T4_empty_tags"] = isinstance(r3, str)

# Verify tags stored in metadata
store2 = VectorStore(persist_path=Path(tempfile.mkdtemp()))
tagged_id = semantic_write("adr", "Use Redis for caching.", store2, tags=["redis", "cache"])
results_g2["T5_id_returned"] = isinstance(tagged_id, str)
results2 = store2.query("Redis caching", k=1)
results_g2["T6_metadata_has_type"] = results2[0]["metadata"]["type"] == "adr" if results2 else False

# ---------------------------------------------------------------------------
# Group 3 — semantic_query()  [8 tests]
# ---------------------------------------------------------------------------

results_g3: dict[str, bool] = {}

store3 = VectorStore(persist_path=Path(tempfile.mkdtemp()))
for text in ["PostgreSQL", "Redis", "Kubernetes", "Python", "FastAPI"]:
    semantic_store(f"{text} is a technology used in modern systems.", "doc", store3)

mock_summary = "Mocked context summary."
with patch('compression.context_builder.compress_memories_full', return_value=(mock_summary, 0.85, "abstractive")):
    r = semantic_query("technology stack", 5, store3)

results_g3["T1_returns_dict"] = isinstance(r, dict)
results_g3["T2_has_summary"] = "summary" in r
results_g3["T3_has_sources"] = "sources" in r
results_g3["T4_has_confidence"] = "confidence" in r
results_g3["T5_summary_is_str"] = isinstance(r.get("summary"), str)
results_g3["T6_sources_is_list"] = isinstance(r.get("sources"), list)
results_g3["T7_confidence_in_range"] = 0.0 <= r.get("confidence", -1) <= 1.0
results_g3["T8_no_extra_keys"] = set(r.keys()) == {"summary", "sources", "confidence", "mode"}

# ---------------------------------------------------------------------------
# Group 4 — write_file_safe()  [10 tests]
# ---------------------------------------------------------------------------

results_g4: dict[str, bool] = {}
ws = Path(tempfile.mkdtemp())
logs = Path(tempfile.mkdtemp())
ft._WORKSPACE = ws
ft._LOGS = logs

r = ft.write_file_safe("hello.txt", "world")
results_g4["T1_success_true"] = r == {"success": True}
results_g4["T2_file_exists"] = (ws / "hello.txt").exists()
results_g4["T3_content_correct"] = (ws / "hello.txt").read_text() == "world"

r2 = ft.write_file_safe("a/b/c.txt", "nested")
results_g4["T4_nested_dirs"] = r2 == {"success": True} and (ws / "a/b/c.txt").exists()

# Traversals
results_g4["T5_traversal_1"] = ft.write_file_safe("../evil.txt", "x") == {"success": False}
results_g4["T6_traversal_2"] = ft.write_file_safe("../../evil.txt", "x") == {"success": False}
results_g4["T7_absolute_path"] = ft.write_file_safe("/etc/passwd", "x") == {"success": False}
results_g4["T8_win_absolute"] = ft.write_file_safe("C:\\Windows\\evil.txt", "x") == {"success": False}

# Audit log
ft.write_file_safe("audit_test.txt", "test")
log_file = logs / "writes.jsonl"
results_g4["T9_log_exists"] = log_file.exists()

entries = [json.loads(l) for l in log_file.read_text().strip().split('\n') if l]
results_g4["T10_log_has_fields"] = all(
    "timestamp" in e and "path" in e and "bytes" in e for e in entries
)

# ---------------------------------------------------------------------------
# Group 5 — read_file_compress()  [8 tests]
# ---------------------------------------------------------------------------

results_g5: dict[str, bool] = {}
ws2 = Path(tempfile.mkdtemp())
logs2 = Path(tempfile.mkdtemp())
ft._WORKSPACE = ws2
ft._LOGS = logs2
(ws2 / "sample.txt").write_text("FastAPI is a modern Python framework for building APIs.")

mock_store = MagicMock()
mock_store.store.return_value = "abc-def-123"

with patch('tools.file_tools.summarize_text', return_value='FastAPI summary.'):
    r = ft.read_file_compress("sample.txt", mock_store)

results_g5["T1_has_summary"] = r.get("summary") == "FastAPI summary."
results_g5["T2_has_id"] = r.get("id") == "abc-def-123"
results_g5["T3_source_matches"] = r.get("source") == "sample.txt"
results_g5["T4_error_is_none"] = r.get("error") is None
results_g5["T5_store_called"] = mock_store.store.called
results_g5["T6_only_summary_to_store"] = mock_store.store.call_args[0][0].content == "FastAPI summary."

# Not found
r2 = ft.read_file_compress("nonexistent.txt", mock_store)
results_g5["T7_not_found_error"] = r2.get("error") is not None and r2.get("summary") is None

# Traversal blocked
r3 = ft.read_file_compress("../../secret.txt", mock_store)
results_g5["T8_traversal_blocked"] = r3.get("summary") is None

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

GROUPS = {
    "Group 1 — semantic_store()  [10 tests]":    results_g1,
    "Group 2 — semantic_write()  [6 tests]":     results_g2,
    "Group 3 — semantic_query()  [8 tests]":     results_g3,
    "Group 4 — write_file_safe() [10 tests]":    results_g4,
    "Group 5 — read_file_compress() [8 tests]":  results_g5,
}

total_pass = 0
total_tests = 0
failures: list[tuple[str, str]] = []

for group_name, results in GROUPS.items():
    group_pass = sum(1 for v in results.values() if v)
    group_total = len(results)
    total_pass += group_pass
    total_tests += group_total

    print(f"\n{'='*60}")
    print(f"{group_name}   {group_pass}/{group_total}")
    print(f"{'='*60}")
    print(f"{'Test':<35} {'Result'}")
    print(f"{'-'*35} {'-'*6}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{name:<35} {status}")
        if not passed:
            failures.append((group_name, name))

print(f"\n{'='*60}")
print(f"TOTAL: {total_pass}/{total_tests} passed")
print(f"Tool Integrity Score: {total_pass/total_tests*100:.1f}%")

if failures:
    print(f"\n{'='*60}")
    print("FAILURES:")
    print(f"{'='*60}")
    for group, name in failures:
        print(f"  FAIL  {group} :: {name}")
else:
    print("\nAll tests passed.")
