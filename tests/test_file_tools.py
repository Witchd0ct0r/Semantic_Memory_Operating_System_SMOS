from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import smos.tools.file_tools as ft


@pytest.fixture(autouse=True)
def patch_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    monkeypatch.setattr(ft, "_WORKSPACE", workspace)
    monkeypatch.setattr(ft, "_LOGS", logs)
    return workspace


def test_write_file_safe_creates_file(tmp_path: Path) -> None:
    result = ft.write_file_safe("test.txt", "hello world")
    assert result == {"success": True}
    assert (ft._WORKSPACE / "test.txt").read_text(encoding="utf-8") == "hello world"


def test_write_file_safe_creates_nested_dirs() -> None:
    result = ft.write_file_safe("a/b/c/file.txt", "content")
    assert result == {"success": True}
    assert (ft._WORKSPACE / "a" / "b" / "c" / "file.txt").exists()


def test_write_file_logs_entry() -> None:
    ft.write_file_safe("logged.txt", "data")
    log_file = ft._LOGS / "writes.jsonl"
    assert log_file.exists()
    import json
    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["path"] == "logged.txt"
    assert entry["bytes"] == 4


def test_write_file_path_traversal_parent_blocked() -> None:
    result = ft.write_file_safe("../../etc/passwd", "evil")
    assert result == {"success": False}


def test_write_file_path_traversal_absolute_blocked() -> None:
    result = ft.write_file_safe("/etc/passwd", "evil")
    assert result == {"success": False}


def test_read_file_not_found() -> None:
    mock_store = MagicMock()
    result = ft.read_file_compress("nonexistent.txt", mock_store)
    assert result["summary"] is None
    assert result["error"] is not None
    mock_store.store.assert_not_called()


def test_read_file_compress_success(tmp_path: Path) -> None:
    workspace = ft._WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "sample.txt").write_text("FastAPI is a modern Python web framework.", encoding="utf-8")

    mock_store = MagicMock()
    mock_store.store.return_value = "abc-123"

    with patch("smos.tools.file_tools.summarize_text", return_value="FastAPI: modern Python web framework."):
        result = ft.read_file_compress("sample.txt", mock_store)

    assert result["summary"] == "FastAPI: modern Python web framework."
    assert result["id"] == "abc-123"
    assert result["source"] == "sample.txt"
    assert result["error"] is None
    mock_store.store.assert_called_once()


def test_read_file_path_traversal_blocked() -> None:
    mock_store = MagicMock()
    result = ft.read_file_compress("../../secret.txt", mock_store)
    assert result["summary"] is None
    assert "blocked" in (result["error"] or "").lower()
