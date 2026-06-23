"""
QA tests for the repository ingestion subsystem.

Coverage:
  - recursive scanning with include/exclude filters
  - invalid/missing paths
  - binary file detection and skipping
  - empty directory handling
  - duplicate detection via ingested_files table
  - bulk_read ordering and error cases
  - import graph extraction (Python + JS/TS)
  - snapshot generation structure
  - store_batch correctness
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import (
    _is_binary,
    _scan_directory,
    _extract_imports,
    _matches_any,
    do_bulk_read,
    do_recursive_semantic_ingest,
    do_semantic_snapshot_repo,
    _FileInfo,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    db_dir = tmp_path / "db"
    return VectorStore(persist_path=db_dir)


def _make_file(parent: Path, name: str, content: str = "hello world content here") -> Path:
    p = parent / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_binary(parent: Path, name: str) -> Path:
    p = parent / name
    p.write_bytes(b"\x00\x01\x02" * 100)
    return p


# ─── Binary detection ────────────────────────────────────────────────────────


def test_is_binary_text_file(tmp: Path) -> None:
    f = _make_file(tmp, "code.py", "print('hello')")
    assert _is_binary(f) is False


def test_is_binary_null_byte(tmp: Path) -> None:
    f = _make_binary(tmp, "blob.bin")
    assert _is_binary(f) is True


def test_is_binary_missing_file(tmp: Path) -> None:
    assert _is_binary(tmp / "nonexistent.bin") is True


# ─── Pattern matching ────────────────────────────────────────────────────────


def test_matches_any_by_name() -> None:
    assert _matches_any("test_foo.py", "tests/test_foo.py", ["test_*.py"])


def test_matches_any_by_rel() -> None:
    assert _matches_any("foo.py", "src/foo.py", ["src/*.py"])


def test_matches_any_no_match() -> None:
    assert not _matches_any("main.py", "src/main.py", ["*.md", "*.json"])


# ─── Directory scanning ──────────────────────────────────────────────────────


def test_scan_empty_directory(tmp: Path) -> None:
    files, skipped = _scan_directory(tmp, True, None, None, 100)
    assert files == []
    assert skipped == []


def test_scan_finds_py_files(tmp: Path) -> None:
    _make_file(tmp, "a.py", "x = 1")
    _make_file(tmp, "b.py", "y = 2")
    files, _ = _scan_directory(tmp, True, None, None, 100)
    assert len(files) == 2
    exts = {fi.extension for fi in files}
    assert exts == {".py"}


def test_scan_skips_unsupported_extension(tmp: Path) -> None:
    _make_file(tmp, "image.png", "not text")
    files, skipped = _scan_directory(tmp, True, None, None, 100)
    assert len(files) == 0
    assert any("unsupported_ext" in s for s in skipped)


def test_scan_skips_git_dir(tmp: Path) -> None:
    git = tmp / ".git"
    git.mkdir()
    (git / "config").write_text("[core]", encoding="utf-8")
    files, _ = _scan_directory(tmp, True, None, None, 100)
    assert files == []


def test_scan_skips_node_modules(tmp: Path) -> None:
    nm = tmp / "node_modules"
    nm.mkdir()
    _make_file(nm, "index.js", "module.exports = {}")
    files, _ = _scan_directory(tmp, True, None, None, 100)
    assert files == []


def test_scan_skips_pycache(tmp: Path) -> None:
    cache = tmp / "__pycache__"
    cache.mkdir()
    _make_file(cache, "mod.cpython-312.pyc", "bytecode")
    files, _ = _scan_directory(tmp, True, None, None, 100)
    assert files == []


def test_scan_include_pattern(tmp: Path) -> None:
    _make_file(tmp, "main.py", "pass")
    _make_file(tmp, "notes.md", "# Notes")
    files, _ = _scan_directory(tmp, True, ["*.py"], None, 100)
    assert len(files) == 1
    assert files[0].extension == ".py"


def test_scan_exclude_pattern(tmp: Path) -> None:
    _make_file(tmp, "main.py", "pass")
    _make_file(tmp, "test_main.py", "pass")
    files, _ = _scan_directory(tmp, True, None, ["test_*.py"], 100)
    names = [fi.path.name for fi in files]
    assert "test_main.py" not in names
    assert "main.py" in names


def test_scan_max_files_cap(tmp: Path) -> None:
    for i in range(10):
        _make_file(tmp, f"f{i}.py", "x")
    files, skipped = _scan_directory(tmp, True, None, None, 5)
    assert len(files) == 5
    assert any("max_files_reached" in s for s in skipped)


def test_scan_non_recursive(tmp: Path) -> None:
    sub = tmp / "subdir"
    sub.mkdir()
    _make_file(tmp, "top.py", "top")
    _make_file(sub, "nested.py", "nested")
    files, _ = _scan_directory(tmp, False, None, None, 100)
    names = [fi.path.name for fi in files]
    assert "top.py" in names
    assert "nested.py" not in names


def test_scan_file_metadata(tmp: Path) -> None:
    f = _make_file(tmp, "code.py", "x = 1  # some content")
    files, _ = _scan_directory(tmp, True, None, None, 100)
    assert len(files) == 1
    fi = files[0]
    assert fi.extension == ".py"
    assert fi.size_bytes == f.stat().st_size
    assert fi.modified_ts > 0
    assert fi.rel_path == "code.py"


# ─── Import graph extraction ─────────────────────────────────────────────────


def test_extract_imports_python_import(tmp: Path) -> None:
    f = tmp / "app.py"
    f.write_text("import os\nimport sys\n", encoding="utf-8")
    edges = _extract_imports(f.read_text(), f)
    targets = [t for _, t in edges]
    assert "os" in targets
    assert "sys" in targets


def test_extract_imports_python_from(tmp: Path) -> None:
    f = tmp / "app.py"
    f.write_text("from pathlib import Path\nfrom typing import Optional\n", encoding="utf-8")
    edges = _extract_imports(f.read_text(), f)
    targets = [t for _, t in edges]
    assert "pathlib" in targets
    assert "typing" in targets


def test_extract_imports_js(tmp: Path) -> None:
    f = tmp / "app.js"
    f.write_text('import React from "react";\nconst x = require("lodash");\n', encoding="utf-8")
    edges = _extract_imports(f.read_text(), f)
    targets = [t for _, t in edges]
    assert "react" in targets
    assert "lodash" in targets


def test_extract_imports_ts(tmp: Path) -> None:
    f = tmp / "app.ts"
    f.write_text('import { Component } from "@angular/core";\n', encoding="utf-8")
    edges = _extract_imports(f.read_text(), f)
    targets = [t for _, t in edges]
    assert "@angular/core" in targets


def test_extract_imports_non_source_file(tmp: Path) -> None:
    f = tmp / "notes.md"
    f.write_text("# Notes\nimport this idea from somewhere\n", encoding="utf-8")
    edges = _extract_imports(f.read_text(), f)
    assert edges == []


# ─── bulk_read ───────────────────────────────────────────────────────────────


def test_bulk_read_empty_list() -> None:
    result = do_bulk_read([])
    assert result["status"] == "success"
    assert result["paths_requested"] == 0
    assert result["results"] == []


def test_bulk_read_single_file(tmp: Path) -> None:
    f = _make_file(tmp, "a.txt", "hello there")
    result = do_bulk_read([str(f)])
    assert result["status"] == "success"
    assert result["paths_read"] == 1
    assert result["results"][0]["content"] == "hello there"
    assert result["results"][0]["error"] is None


def test_bulk_read_preserves_order(tmp: Path) -> None:
    paths = []
    for i in range(5):
        f = _make_file(tmp, f"f{i}.txt", f"content_{i}")
        paths.append(str(f))
    result = do_bulk_read(paths)
    contents = [r["content"] for r in result["results"]]
    assert contents == [f"content_{i}" for i in range(5)]


def test_bulk_read_missing_file(tmp: Path) -> None:
    f_ok = _make_file(tmp, "ok.txt", "ok")
    missing = str(tmp / "missing.txt")
    result = do_bulk_read([str(f_ok), missing])
    assert result["results"][0]["error"] is None
    assert result["results"][1]["error"] == "not_found"


def test_bulk_read_binary_skipped(tmp: Path) -> None:
    _make_binary(tmp, "data.bin")
    f = tmp / "data.bin"
    result = do_bulk_read([str(f)])
    assert result["results"][0]["error"] == "binary"
    assert result["results"][0]["content"] is None


# ─── store_batch ─────────────────────────────────────────────────────────────


def test_store_batch_empty(store: VectorStore) -> None:
    ids = store.store_batch([])
    assert ids == []
    assert store.count() == 0


def test_store_batch_single(store: VectorStore) -> None:
    m = MemoryObject(type="doc", content="a test memory about Python embeddings")
    ids = store.store_batch([m])
    assert len(ids) == 1
    assert store.count() == 1


def test_store_batch_multiple(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="doc", content=f"memory entry number {i} with some content")
        for i in range(10)
    ]
    ids = store.store_batch(memories)
    assert len(ids) == 10
    assert store.count() == 10


def test_store_batch_ids_unique(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="doc", content=f"entry {i} with unique content text")
        for i in range(5)
    ]
    ids = store.store_batch(memories)
    assert len(set(ids)) == 5


def test_store_batch_queryable(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="doc", content="FastAPI is a modern web framework for Python"),
        MemoryObject(type="doc", content="SQLite is a lightweight embedded database"),
    ]
    store.store_batch(memories)
    results = store.query("web framework Python", k=2)
    assert len(results) >= 1
    contents = [r["content"] for r in results]
    assert any("FastAPI" in c for c in contents)


def test_store_batch_across_chunk_boundary(store: VectorStore) -> None:
    # Force multiple chunks (batch_size=4, 10 items)
    memories = [
        MemoryObject(type="doc", content=f"chunk test entry {i} with sufficient text length")
        for i in range(10)
    ]
    ids = store.store_batch(memories, batch_size=4)
    assert len(ids) == 10
    assert store.count() == 10


# ─── ingested_files tracking ─────────────────────────────────────────────────


def test_get_ingested_paths_empty(store: VectorStore) -> None:
    assert store.get_ingested_paths() == set()


def test_mark_files_ingested_batch(store: VectorStore) -> None:
    pairs = [("/a/b/c.py", "id-1"), ("/a/b/d.py", "id-2")]
    store.mark_files_ingested_batch(pairs)
    paths = store.get_ingested_paths()
    assert "/a/b/c.py" in paths
    assert "/a/b/d.py" in paths


def test_mark_files_idempotent(store: VectorStore) -> None:
    pairs = [("/a/file.py", "id-1")]
    store.mark_files_ingested_batch(pairs)
    store.mark_files_ingested_batch(pairs)  # second call should not raise
    paths = store.get_ingested_paths()
    assert "/a/file.py" in paths


# ─── do_recursive_semantic_ingest ────────────────────────────────────────────


def test_ingest_nonexistent_path(store: VectorStore) -> None:
    result = do_recursive_semantic_ingest("/nonexistent/path", store, summarize=False)
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_ingest_not_a_directory(store: VectorStore, tmp: Path) -> None:
    f = _make_file(tmp, "file.py", "x=1")
    result = do_recursive_semantic_ingest(str(f), store, summarize=False)
    assert result["status"] == "error"


def test_ingest_empty_directory(store: VectorStore, tmp: Path) -> None:
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert result["status"] == "success"
    assert result["files_scanned"] == 0
    assert result["memories_created"] == 0


def test_ingest_single_py_file(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "main.py", "def main():\n    print('hello world from the main function')")
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert result["status"] == "success"
    assert result["files_scanned"] == 1
    assert result["memories_created"] == 1
    assert store.count() == 1


def test_ingest_multiple_extensions(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "app.py", "from flask import Flask; app = Flask(__name__)")
    _make_file(tmp, "README.md", "# My App\nA simple Flask application example")
    _make_file(tmp, "config.yaml", "debug: true\nport: 8080")
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert result["files_scanned"] == 3
    assert result["memories_created"] == 3


def test_ingest_skips_binary(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "code.py", "print('hello world from python file')")
    _make_binary(tmp, "image.py")  # .py but binary
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    # binary file is read-error skipped, only text py ingested
    assert result["memories_created"] == 1
    assert len(result.get("errors", [])) >= 1


def test_ingest_deduplicates_on_second_run(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "a.py", "x = 1  # some python code content here")
    r1 = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert r1["memories_created"] == 1

    r2 = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert r2["duplicates_removed"] == 1
    assert r2["memories_created"] == 0
    assert store.count() == 1  # no new memories added


def test_ingest_include_filter(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "main.py", "print('python file content here for testing')")
    _make_file(tmp, "README.md", "# Documentation title for the project")
    result = do_recursive_semantic_ingest(str(tmp), store, include_patterns=["*.py"], summarize=False)
    assert result["files_ingested"] == 1
    assert result["files_scanned"] == 1


def test_ingest_exclude_filter(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "main.py", "print('main module function entry point')")
    _make_file(tmp, "test_main.py", "def test_main(): pass  # unit test function")
    result = do_recursive_semantic_ingest(str(tmp), store, exclude_patterns=["test_*.py"], summarize=False)
    assert result["files_ingested"] == 1


def test_ingest_skips_git(store: VectorStore, tmp: Path) -> None:
    git = tmp / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n  bare = false", encoding="utf-8")
    _make_file(tmp, "app.py", "x = 1  # real source code here")
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert result["memories_created"] == 1  # only app.py


def test_ingest_result_has_all_keys(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "x.py", "print('test content for key verification')")
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    for key in ("status", "files_scanned", "files_ingested", "files_skipped",
                "duplicates_removed", "time_seconds", "memories_created"):
        assert key in result, f"missing key: {key}"


# ─── Large directory (100 files) ─────────────────────────────────────────────


def test_ingest_100_files(store: VectorStore, tmp: Path) -> None:
    for i in range(100):
        _make_file(tmp, f"module_{i:03d}.py", f"# Module {i}\ndef func_{i}():\n    return {i}")
    result = do_recursive_semantic_ingest(str(tmp), store, summarize=False)
    assert result["status"] == "success"
    assert result["files_ingested"] == 100
    assert store.count() == 100


# ─── do_semantic_snapshot_repo ───────────────────────────────────────────────


def test_snapshot_nonexistent_path(store: VectorStore) -> None:
    result = do_semantic_snapshot_repo("/nonexistent/repo", store)
    assert result["status"] == "error"


def test_snapshot_not_directory(store: VectorStore, tmp: Path) -> None:
    f = _make_file(tmp, "file.py", "x=1")
    result = do_semantic_snapshot_repo(str(f), store)
    assert result["status"] == "error"


def test_snapshot_empty_repo(store: VectorStore, tmp: Path) -> None:
    with patch("smos.tools.ingest_tools._arch_summary_via_llm", return_value="Empty repo."):
        result = do_semantic_snapshot_repo(str(tmp), store)
    assert result["status"] == "success"
    assert result["file_count"] == 0
    assert result["memories_created"] == 0


def test_snapshot_structure(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "main.py", "import os\nfrom pathlib import Path\nprint('hello')")
    _make_file(tmp, "README.md", "# Project\nThis is a test project for snapshot generation")
    (tmp / "src").mkdir()
    _make_file(tmp / "src", "utils.py", "def helper():\n    return True  # utility function")

    with patch("smos.tools.ingest_tools._arch_summary_via_llm", return_value="Test repo architecture."):
        result = do_semantic_snapshot_repo(str(tmp), store)

    assert result["status"] == "success"
    assert "repository_name" in result
    assert "language_breakdown" in result
    assert "Python" in result["language_breakdown"]
    assert "Markdown" in result["language_breakdown"]
    assert "major_modules" in result
    assert "src" in result["major_modules"]
    assert "import_graph_edge_count" in result
    assert result["import_graph_edge_count"] > 0
    assert "snapshot_memory_id" in result
    assert result["snapshot_memory_id"] is not None
    assert result["architecture_summary"] == "Test repo architecture."


def test_snapshot_detects_important_files(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "README.md", "# Project documentation header\nThis is the readme")
    _make_file(tmp, "main.py", "def main(): pass  # entry point")

    with patch("smos.tools.ingest_tools._arch_summary_via_llm", return_value="Summary."):
        result = do_semantic_snapshot_repo(str(tmp), store)

    important = result["important_files"]
    assert any("README" in f or "readme" in f.lower() for f in important)
    assert any("main.py" in f for f in important)


def test_snapshot_stores_queryable_memory(store: VectorStore, tmp: Path) -> None:
    _make_file(tmp, "server.py", "from fastapi import FastAPI\napp = FastAPI()  # api server")

    with patch("smos.tools.ingest_tools._arch_summary_via_llm",
               return_value="FastAPI server repository architecture."):
        do_semantic_snapshot_repo(str(tmp), store)

    results = store.query("repository architecture snapshot", k=5)
    assert len(results) >= 1


# ─── Sandbox safety ──────────────────────────────────────────────────────────


def test_ingest_null_byte_in_path(store: VectorStore) -> None:
    # Path with null byte should not crash — it's just not found
    result = do_recursive_semantic_ingest("/tmp/valid\x00path", store, summarize=False)
    assert result["status"] == "error"
