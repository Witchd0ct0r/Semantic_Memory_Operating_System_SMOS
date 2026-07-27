"""Ingest sandbox/toolchain-benchmark/corpus/*.md into an isolated .smos/ store,
via the same do_ingest_document_schema() path production ingestion uses (Phase
5.3/5.5a) — not the lossy recursive-scan path — so the benchmark measures the
real structure-preserving, reference-extracting, domain-tagged retrieval SMOS
actually ships, not a degraded fallback.

Run from anywhere; paths are resolved relative to this file. Idempotent:
do_ingest_document_schema upserts by (system, native_id), so re-running after
a corpus change resegments/replaces rather than duplicating.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SANDBOX_DIR = _SCRIPT_DIR.parent
_CORPUS_DIR = _SANDBOX_DIR / "corpus"
_STORE_DIR = _SANDBOX_DIR / ".smos"
_REPO_ROOT = _SANDBOX_DIR.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


def main() -> None:
    pages = sorted(p for p in _CORPUS_DIR.glob("*.md") if p.name != "SOURCE.md")
    if not pages:
        raise SystemExit(f"No corpus pages found in {_CORPUS_DIR} — run pull_corpus.sh first")

    store = VectorStore(persist_path=_STORE_DIR)
    adapter = MarkdownAdapter()

    for page in pages:
        text = page.read_text(encoding="utf-8")
        payload = markdown_to_payload(
            text, system="kubernetes-docs", native_id=page.stem, doc_name=page.stem
        )
        payload.domain_tags = ["kubernetes"]
        result = do_ingest_document_schema(payload, store, adapter=adapter)
        status = result.get("status", "?")
        print(f"{page.name}: {status} (doc_id={result.get('doc_id', '?')})")
        if status not in ("complete", "resumed"):
            print(f"  WARNING non-complete status, full result: {result}")

    print(f"\nIngested {len(pages)} pages into {_STORE_DIR}")


if __name__ == "__main__":
    main()
