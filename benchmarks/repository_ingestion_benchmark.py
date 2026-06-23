"""
Repository Ingestion Benchmark Suite
=====================================
Measures all pipeline phases independently so bottlenecks are clearly attributed.

Phases timed:
  1. Scan        -  filesystem walk only (no I/O)
  2. Read        -  parallel file read (no embedding or LLM)
  3. Embed       -  batch SentenceTransformer encoding only
  4. Full ingest  -  scan + read + embed + SQLite/FAISS store (summarize=False)

Corpus sizes: 100 / 1000 / 5000 files

Targets:
  - Scan          > 200 files/sec
  - Embed (batch) > 250 embeds/sec
  - Full ingest (fast mode): best-effort, should scale linearly

Run:
    python benchmarks/repository_ingestion_benchmark.py
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Optional

# Ensure the repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smos.memory.embeddings import embed_batch
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import (
    _scan_directory,
    do_bulk_read,
    do_recursive_semantic_ingest,
)

# --- Targets -----------------------------------------------------------------

TARGET_SCAN_FILES_PER_SEC = 200
TARGET_EMBED_PER_SEC = 250

# --- Corpus generation -------------------------------------------------------

_PYTHON_TEMPLATE = """\
\"\"\"Module {i}: auto-generated benchmark file.\"\"\"
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional, List

MODULE_ID = {i}
_CONSTANT = "value_{i}"


class Service{i}:
    \"\"\"Service class for module {i}.\"\"\"

    def __init__(self, name: str) -> None:
        self.name = name
        self._cache: dict[str, str] = {{}}

    def process(self, data: str) -> Optional[str]:
        if not data:
            return None
        result = data.upper() + f"_{{MODULE_ID}}"
        self._cache[data] = result
        return result

    def batch_process(self, items: List[str]) -> List[str]:
        return [self.process(item) for item in items if item]


def main_{i}() -> None:
    svc = Service{i}("bench_{i}")
    result = svc.process("test_input")
    print(f"Module {{MODULE_ID}}: {{result}}")


if __name__ == "__main__":
    main_{i}()
"""


def _build_corpus(root: Path, n_files: int) -> None:
    """Create n_files Python files across a realistic directory structure."""
    dirs = ["src/core", "src/api", "src/utils", "src/db", "tests", "docs", "config"]
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)

    # Write some non-.py files too (README, config)
    (root / "README.md").write_text(
        "# Benchmark Repository\n\nAuto-generated for SMOS ingestion benchmarks.\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "bench"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    for i in range(n_files):
        subdir = dirs[i % len(dirs)]
        path = root / subdir / f"module_{i:04d}.py"
        path.write_text(_PYTHON_TEMPLATE.format(i=i), encoding="utf-8")


# --- Individual phase benchmarks ---------------------------------------------


def bench_scan(root: Path, n_files: int) -> dict:
    gc.collect()
    t0 = time.perf_counter()
    files, skipped = _scan_directory(root, True, None, None, n_files + 100)
    elapsed = time.perf_counter() - t0
    rate = len(files) / elapsed if elapsed > 0 else float("inf")
    return {
        "phase": "scan",
        "n_files": n_files,
        "found": len(files),
        "skipped": len(skipped),
        "elapsed_s": round(elapsed, 4),
        "files_per_sec": round(rate, 1),
        "pass": rate >= TARGET_SCAN_FILES_PER_SEC,
    }


def bench_bulk_read(root: Path, n_files: int) -> dict:
    from smos.tools.ingest_tools import _scan_directory, _FileInfo
    files, _ = _scan_directory(root, True, None, None, n_files)
    paths = [str(fi.path) for fi in files[:n_files]]

    gc.collect()
    t0 = time.perf_counter()
    result = do_bulk_read(paths)
    elapsed = time.perf_counter() - t0

    rate = len(paths) / elapsed if elapsed > 0 else float("inf")
    return {
        "phase": "bulk_read",
        "n_files": n_files,
        "read_ok": result["paths_read"],
        "elapsed_s": round(elapsed, 4),
        "files_per_sec": round(rate, 1),
    }


def bench_embed(n_texts: int, batch_size: int = 64) -> dict:
    # ~190-char synthetic texts representing compressed summaries or short metadata.
    # Use --realistic flag (not yet implemented) for 500-char full-file-content timing.
    texts = [
        f"Module {i}: processes data and returns results. "
        f"Handles edge cases including None values and empty lists. "
        f"Performance O(n) where n is input length. ID: {i}."
        for i in range(n_texts)
    ]

    gc.collect()
    t0 = time.perf_counter()
    all_vecs: list = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        vecs = embed_batch(chunk)
        all_vecs.extend(vecs)
    elapsed = time.perf_counter() - t0

    rate = n_texts / elapsed if elapsed > 0 else float("inf")
    return {
        "phase": "embed_batch",
        "n_texts": n_texts,
        "batch_size": batch_size,
        "vectors_produced": len(all_vecs),
        "elapsed_s": round(elapsed, 4),
        "embeds_per_sec": round(rate, 1),
        "pass": rate >= TARGET_EMBED_PER_SEC,
    }


def bench_full_ingest(root: Path, n_files: int, db_path: Path) -> dict:
    store = VectorStore(persist_path=db_path)

    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()

    result = do_recursive_semantic_ingest(
        str(root),
        store,
        recursive=True,
        summarize=False,  # no LLM  -  measures embed+store throughput
        store_raw_metadata=True,
    )

    elapsed = time.perf_counter() - t0
    _, mem_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    ingested = result.get("files_ingested", 0)
    rate = ingested / elapsed if elapsed > 0 else float("inf")

    # Query latency after ingestion
    q_times = []
    for _ in range(5):
        qt0 = time.perf_counter()
        store.query("process data return results", k=5)
        q_times.append(time.perf_counter() - qt0)
    p95_query_ms = round(sorted(q_times)[4] * 1000, 2)

    faiss_count = store.count()
    store.close()

    return {
        "phase": "full_ingest",
        "n_files_target": n_files,
        "files_scanned": result.get("files_scanned", 0),
        "files_ingested": ingested,
        "files_skipped": result.get("files_skipped", 0),
        "duplicates_removed": result.get("duplicates_removed", 0),
        "elapsed_s": round(elapsed, 2),
        "files_per_sec": round(rate, 1),
        "faiss_count": faiss_count,
        "peak_memory_mb": round(mem_peak / 1_048_576, 2),
        "query_p95_ms_after_ingest": p95_query_ms,
        "errors": result.get("errors", [])[:5],
        "status": result.get("status"),
    }


# --- Report printing ---------------------------------------------------------


def _print_result(r: dict) -> None:
    phase = r.get("phase", "?")
    ok = r.get("pass", True)
    mark = "OK" if ok else "!!"

    if phase == "scan":
        print(f"  [{mark}] Scan {r['n_files']:>5} files: {r['elapsed_s']:.3f}s  "
              f"({r['files_per_sec']:.0f} files/sec)  "
              f"[target: >{TARGET_SCAN_FILES_PER_SEC}]  {'PASS' if r['pass'] else 'FAIL'}")

    elif phase == "bulk_read":
        print(f"  [  ] BulkRead {r['n_files']:>5} files: {r['elapsed_s']:.3f}s  "
              f"({r['files_per_sec']:.0f} files/sec)")

    elif phase == "embed_batch":
        print(f"  [{mark}] Embed {r['n_texts']:>5} texts (batch={r['batch_size']}): "
              f"{r['elapsed_s']:.3f}s  ({r['embeds_per_sec']:.0f} embeds/sec)  "
              f"[target: >{TARGET_EMBED_PER_SEC}]  {'PASS' if r['pass'] else 'FAIL'}")

    elif phase == "full_ingest":
        errors_note = f"  [{len(r['errors'])} errors]" if r.get("errors") else ""
        print(f"  [  ] FullIngest {r['files_ingested']:>5} files: {r['elapsed_s']:.2f}s  "
              f"({r['files_per_sec']:.1f} files/sec)  "
              f"FAISS={r['faiss_count']}  "
              f"mem={r['peak_memory_mb']:.1f}MB  "
              f"query_p95={r['query_p95_ms_after_ingest']}ms"
              f"{errors_note}")


# --- Main --------------------------------------------------------------------


def run_benchmarks(sizes: list[int] = (100, 1000, 5000)) -> list[dict]:
    all_results: list[dict] = []
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_"))

    try:
        print("\n" + "=" * 70)
        print("  SMOS Repository Ingestion Benchmark")
        print("=" * 70)

        # Pre-build the largest corpus once, smaller runs use a subset
        print(f"\n  Building corpus ({max(sizes)} files)...", end=" ", flush=True)
        corpus_root = tmp_root / "corpus"
        corpus_root.mkdir()
        _build_corpus(corpus_root, max(sizes))
        print("done.")

        # Phase: Scan
        print("\n[ Scan - filesystem walk, no I/O ]")
        for n in sizes:
            r = bench_scan(corpus_root, n)
            _print_result(r)
            all_results.append(r)

        # Phase: Bulk Read
        print("\n[ BulkRead - parallel I/O, no embedding ]")
        for n in sizes:
            r = bench_bulk_read(corpus_root, n)
            _print_result(r)
            all_results.append(r)

        # Warmup: load SentenceTransformer model before timing any embed phase
        print("\n  Warming up SentenceTransformer model...", end=" ", flush=True)
        _warmup = ["warmup text for model initialization"] * 64
        embed_batch(_warmup)
        embed_batch(_warmup)  # second pass to fill JIT caches
        print("done.")

        # Phase: Embed
        print("\n[ Embed - batch SentenceTransformer (~190-char synthetic texts) ]")
        print("  (Full-ingest section below shows real-file throughput at 500 chars/file)")
        for n in sizes:
            r = bench_embed(n, batch_size=64)
            _print_result(r)
            all_results.append(r)

        # Phase: Full Ingest
        print("\n[ Full Ingest - scan+read+embed+store (summarize=False) ]")
        for n in sizes:
            db_path = tmp_root / f"db_{n}"
            db_path.mkdir()
            # Build a fresh sub-corpus for each size to avoid dedup skipping
            sub_root = tmp_root / f"corpus_{n}"
            sub_root.mkdir()
            _build_corpus(sub_root, n)

            r = bench_full_ingest(sub_root, n, db_path)
            _print_result(r)
            all_results.append(r)

        # Summary
        print("\n" + "-" * 70)
        print("  Summary")
        print("-" * 70)
        scan_pass = all(r.get("pass", True) for r in all_results if r["phase"] == "scan")
        embed_pass = all(r.get("pass", True) for r in all_results if r["phase"] == "embed_batch")
        ingest_ok = all(r.get("status") == "success" for r in all_results if r["phase"] == "full_ingest")

        print(f"  Scan target  (>{TARGET_SCAN_FILES_PER_SEC} files/sec):   {'PASS' if scan_pass else 'FAIL'}")
        print(f"  Embed target (>{TARGET_EMBED_PER_SEC} embeds/sec): {'PASS' if embed_pass else 'FAIL'}")
        print(f"  Full ingest no crash:             {'PASS' if ingest_ok else 'FAIL'}")
        print()

        # Bottleneck analysis
        if all_results:
            full_results = [r for r in all_results if r["phase"] == "full_ingest"]
            if full_results:
                largest = max(full_results, key=lambda r: r["n_files_target"])
                embed_results = [r for r in all_results
                                 if r["phase"] == "embed_batch"
                                 and r["n_texts"] == largest["n_files_target"]]
                if embed_results:
                    embed_frac = embed_results[0]["elapsed_s"] / largest["elapsed_s"] * 100
                    print(f"  Bottleneck analysis ({largest['n_files_target']} files):")
                    print(f"    Total ingest time: {largest['elapsed_s']:.2f}s")
                    print(f"    Embed portion: ~{embed_frac:.0f}% of ingest time")
                    print(f"    Query P95 after ingest: {largest['query_p95_ms_after_ingest']}ms")

        print("=" * 70)
        return all_results

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SMOS Repository Ingestion Benchmark")
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=[100, 1000, 5000],
        help="Corpus sizes to benchmark (default: 100 1000 5000)"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Run only 100-file benchmark (fast CI check)"
    )
    args = parser.parse_args()

    sizes = [100] if args.quick else args.sizes
    results = run_benchmarks(sizes)

    # Exit non-zero if any target failed
    failed = [r for r in results if "pass" in r and not r["pass"]]
    sys.exit(1 if failed else 0)
