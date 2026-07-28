from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / ".claude" / "skills" / "smos-doc-sync" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import sync_docs  # noqa: E402  (sys.path insert above must run first)

from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "docs" / "runbooks").mkdir(parents=True)
    (root / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (root / "docs" / "superpowers" / "plans").mkdir(parents=True)

    (root / "docs" / "HANDOFF.md").write_text("# Handoff\nCurrent state.\n", encoding="utf-8")
    (root / "README.md").write_text("# Readme\nProject overview.\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text("# Architecture\nData flow.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Instructions\nHow to work here.\n", encoding="utf-8")
    (root / "docs" / "runbooks" / "environment.md").write_text(
        "# Environment\nPython notes.\n", encoding="utf-8"
    )
    (root / "docs" / "superpowers" / "specs" / "one-design.md").write_text(
        "# Spec One\nDesign details.\n", encoding="utf-8"
    )
    (root / "docs" / "superpowers" / "plans" / "one-plan.md").write_text(
        "# Plan One\nTask list.\n", encoding="utf-8"
    )
    return root


_ALL_NATIVE_IDS = {
    "docs/HANDOFF.md",
    "README.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "docs/runbooks/environment.md",
    "docs/superpowers/specs/one-design.md",
    "docs/superpowers/plans/one-plan.md",
}


def test_discover_docs_finds_every_curated_source(doc_tree: Path) -> None:
    found = sync_docs.discover_docs(doc_tree)
    native_ids = {p.relative_to(doc_tree).as_posix() for p, _, _ in found}
    assert native_ids == _ALL_NATIVE_IDS


def test_first_run_ingests_every_doc(doc_tree: Path, store: VectorStore, tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert set(summary["ingested"]) == _ALL_NATIVE_IDS
    assert summary["skipped"] == []
    assert summary["quarantined"] == []
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest.keys()) == _ALL_NATIVE_IDS


def test_second_run_with_no_changes_ingests_nothing(
    doc_tree: Path, store: VectorStore, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    sync_docs.sync_docs(doc_tree, store, manifest_path)

    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert summary["ingested"] == []
    assert set(summary["skipped"]) == _ALL_NATIVE_IDS


def test_changing_one_doc_reingests_only_that_doc(
    doc_tree: Path, store: VectorStore, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    sync_docs.sync_docs(doc_tree, store, manifest_path)

    (doc_tree / "docs" / "HANDOFF.md").write_text(
        "# Handoff\nUpdated state.\n", encoding="utf-8"
    )

    summary = sync_docs.sync_docs(doc_tree, store, manifest_path)

    assert summary["ingested"] == ["docs/HANDOFF.md"]
    assert set(summary["skipped"]) == _ALL_NATIVE_IDS - {"docs/HANDOFF.md"}
