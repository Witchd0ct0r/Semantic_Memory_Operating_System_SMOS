"""Sync this project's own curated documents into SMOS via the schema-driven
ingestion path (IngestionPayload / do_ingest_document_schema), with
content-hash-based change detection so only docs that changed since the
last run are re-ingested. See ../SKILL.md for when to run this.

Usage: python sync_docs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.contract import content_hash
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

DOC_SOURCES: list[tuple[str, str, list[str]]] = [
    ("docs/HANDOFF.md", "handoff", ["smos-project", "handoff"]),
    ("README.md", "readme", ["smos-project", "readme"]),
    ("ARCHITECTURE.md", "architecture", ["smos-project", "architecture"]),
    ("docs/API.md", "api-reference", ["smos-project", "api"]),
    ("CLAUDE.md", "instructions", ["smos-project", "instructions"]),
    ("docs/runbooks/*.md", "runbook", ["smos-project", "runbook"]),
    ("docs/superpowers/specs/**/*.md", "spec", ["smos-project", "spec"]),
    ("docs/superpowers/plans/**/*.md", "plan", ["smos-project", "plan"]),
]

MANIFEST_FILENAME = "doc_sync_manifest.json"


def discover_docs(repo_root: Path) -> list[tuple[Path, str, list[str]]]:
    """Expand DOC_SOURCES into (path, doc_type, domain_tags) triples for
    files that currently exist, deduplicated and sorted by resolved path for
    deterministic ordering."""
    seen: dict[Path, tuple[str, list[str]]] = {}
    for pattern, doc_type, tags in DOC_SOURCES:
        for path in repo_root.glob(pattern):
            if path.is_file():
                seen.setdefault(path.resolve(), (doc_type, tags))
    return [
        (path, doc_type, tags)
        for path, (doc_type, tags) in sorted(seen.items(), key=lambda kv: str(kv[0]))
    ]


def load_manifest(manifest_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_manifest(manifest_path: Path, manifest: dict[str, str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def sync_docs(repo_root: Path, store: VectorStore, manifest_path: Path) -> dict:
    """Ingest every doc in DOC_SOURCES whose content_hash differs from the
    manifest's last-recorded hash (or is missing from the manifest). Returns
    {"ingested": [...], "skipped": [...], "quarantined": [...]} (native_id
    strings). Persists the manifest with updated hashes for every doc that
    was successfully (re-)ingested."""
    manifest = load_manifest(manifest_path)
    docs = discover_docs(repo_root)
    adapter = MarkdownAdapter()

    ingested: list[str] = []
    skipped: list[str] = []
    quarantined: list[str] = []

    for path, doc_type, tags in docs:
        native_id = path.resolve().relative_to(repo_root.resolve()).as_posix()
        text = path.read_text(encoding="utf-8")
        text_hash = content_hash(text)

        if manifest.get(native_id) == text_hash:
            skipped.append(native_id)
            continue

        payload = markdown_to_payload(
            text, system="smos-project-docs", native_id=native_id, doc_name=path.name
        )
        payload.doc_type = doc_type
        payload.domain_tags = tags
        payload.metadata = {"repo_path": native_id}

        result = do_ingest_document_schema(payload, store, adapter=adapter)
        if result["status"] == "success":
            ingested.append(native_id)
            manifest[native_id] = text_hash
        else:
            quarantined.append(native_id)

    save_manifest(manifest_path, manifest)
    return {"ingested": ingested, "skipped": skipped, "quarantined": quarantined}


if __name__ == "__main__":
    store = VectorStore(persist_path=_REPO_ROOT / ".smos")
    manifest_path = _REPO_ROOT / ".smos" / MANIFEST_FILENAME
    summary = sync_docs(_REPO_ROOT, store, manifest_path)
    print(json.dumps(summary, indent=2))
