"""Ingest sandbox/eu-law-benchmark/corpus/*.txt into an isolated .smos/ store,
via do_ingest_document_schema(..., adapter=LegalAdapter()) -- the same
structure-preserving, reference-extracting path
tests/test_legal_adapter.py already proves end-to-end on a small fixture,
run here on real EUR-Lex text instead.

Run from anywhere; paths resolved relative to this file. Idempotent:
do_ingest_document_schema upserts by (system, native_id).
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

from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

_DOCS = [
    ("reg_2018_1807.txt", "reg-2018-1807", "Regulation (EU) 2018/1807"),
    ("gdpr_articles_1-4.txt", "gdpr-articles-1-4", "GDPR Articles 1-4"),
]


def main() -> None:
    store = VectorStore(persist_path=_STORE_DIR)
    adapter = LegalAdapter()

    for filename, native_id, doc_name in _DOCS:
        path = _CORPUS_DIR / filename
        if not path.exists():
            raise SystemExit(f"{path} not found -- run pull_corpus.py first")
        text = path.read_text(encoding="utf-8")
        payload = markdown_to_payload(
            text, system="eu-law-corpus", native_id=native_id, doc_name=doc_name
        )
        payload.domain_tags = ["eu-law"]
        result = do_ingest_document_schema(payload, store, adapter=adapter)
        print(
            f"{filename}: status={result.get('status')} "
            f"doc_id={result.get('doc_id')} "
            f"segments={result.get('segment_count')} "
            f"sections={result.get('section_count')} "
            f"references={result.get('reference_count')}"
        )
        if result.get("status") != "success":
            print(f"  WARNING non-success status, full result: {result}")

    print(f"\nIngested {len(_DOCS)} documents into {_STORE_DIR}")


if __name__ == "__main__":
    main()
