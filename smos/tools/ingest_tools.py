"""
Repository ingestion subsystem for SMOS.

Public callables (called by server.py MCP tool wrappers):
    do_recursive_semantic_ingest()
    do_bulk_read()
    do_semantic_snapshot_repo()

Internal helpers are prefixed with underscore.
"""
from __future__ import annotations

import concurrent.futures
import fnmatch
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from smos.llm.client import get_llm_client, OLLAMA_MODEL
from smos.llm.summarizer import summarize_text
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

# ─── Filesystem constants ────────────────────────────────────────────────────

_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".pytest_cache", "build", "dist", "target", ".tox",
    ".mypy_cache", ".ruff_cache", ".eggs", ".cache", ".idea", ".vscode",
})

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".rst", ".yaml", ".yml",
    ".json", ".toml", ".ini", ".cfg",
})

_LANG_MAP: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".jsx": "JavaScript", ".java": "Java",
    ".go": "Go", ".rs": "Rust", ".c": "C", ".cpp": "C++",
    ".h": "C/C++", ".hpp": "C++", ".md": "Markdown", ".txt": "Text",
    ".rst": "reStructuredText", ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".toml": "TOML", ".ini": "INI", ".cfg": "Config",
}

_IMPORTANT_FILENAMES: frozenset[str] = frozenset({
    "readme.md", "readme.txt", "readme.rst",
    "main.py", "app.py", "server.py", "index.py",
    "index.js", "index.ts", "main.js", "main.ts", "app.js", "app.ts",
    "main.go", "main.rs", "main.c", "main.cpp",
    "package.json", "requirements.txt", "pyproject.toml", "cargo.toml",
    "go.mod", "build.gradle", "pom.xml", "makefile", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
})

_MAX_CONTENT_BYTES: int = 50_000   # file read cap
_LLM_MAX_INPUT_CHARS: int = 10_000  # cap before sending to Ollama
# 500 chars keeps most import lists + function signatures within all-MiniLM-L6-v2's
# 256-token limit while maintaining ~3× higher embed throughput vs 2000-char content.
_QUICK_SUMMARY_CHARS: int = 500
_BATCH_EMBED_SIZE: int = 64
_MAX_PARALLEL_READS: int = 16

# ─── Import-graph regex ──────────────────────────────────────────────────────

_PY_IMPORT_RE = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\t ]+))",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:.*?\s+from\s+)?['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)

# ─── Data types ──────────────────────────────────────────────────────────────


@dataclass
class _FileInfo:
    path: Path
    rel_path: str
    extension: str
    size_bytes: int
    modified_ts: float

    def tags(self, repo_root: str) -> list[str]:
        return [
            "file",
            str(self.path),
            self.extension.lstrip("."),
            f"repo:{repo_root}",
        ]


# ─── Private helpers ─────────────────────────────────────────────────────────


def _is_binary(path: Path, sample: int = 8192) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(sample)
    except OSError:
        return True


def _matches_any(name: str, rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


def _scan_directory(
    root: Path,
    recursive: bool,
    include_patterns: Optional[list[str]],
    exclude_patterns: Optional[list[str]],
    max_files: int,
) -> tuple[list[_FileInfo], list[str]]:
    """Walk directory tree; return (files_to_ingest, skip_reasons)."""
    files: list[_FileInfo] = []
    skipped: list[str] = []

    walker = root.rglob("*") if recursive else root.glob("*")

    for entry in walker:
        if len(files) >= max_files:
            skipped.append(f"max_files_reached:{max_files}")
            break

        if not entry.is_file():
            continue

        # Skip banned directory names anywhere in the path
        path_parts = entry.relative_to(root).parts[:-1]
        if any(p in _SKIP_DIRS for p in path_parts):
            continue

        rel = str(entry.relative_to(root))
        name = entry.name
        ext = entry.suffix.lower()

        if ext not in _SUPPORTED_EXTENSIONS:
            skipped.append(f"unsupported_ext:{ext}")
            continue

        if include_patterns and not _matches_any(name, rel, include_patterns):
            skipped.append(f"include_filter:{rel}")
            continue

        if exclude_patterns and _matches_any(name, rel, exclude_patterns):
            skipped.append(f"exclude_filter:{rel}")
            continue

        try:
            st = entry.stat()
            files.append(_FileInfo(
                path=entry,
                rel_path=rel,
                extension=ext,
                size_bytes=st.st_size,
                modified_ts=st.st_mtime,
            ))
        except OSError as exc:
            skipped.append(f"stat_error:{exc}")

    return files, skipped


def _read_file(fi: _FileInfo) -> tuple[_FileInfo, Optional[str], Optional[str]]:
    """Read one file; return (info, content_or_None, error_or_None)."""
    if _is_binary(fi.path):
        return fi, None, "binary"
    try:
        raw = fi.path.read_bytes()[:_MAX_CONTENT_BYTES]
        return fi, raw.decode("utf-8", errors="replace"), None
    except OSError as exc:
        return fi, None, str(exc)


def _extract_imports(content: str, path: Path) -> list[tuple[str, str]]:
    """Lightweight regex-based import extraction for Python and JS/TS."""
    src = str(path)
    ext = path.suffix.lower()
    edges: list[tuple[str, str]] = []

    if ext == ".py":
        for m in _PY_IMPORT_RE.finditer(content):
            tgt = (m.group(1) or m.group(2) or "").split(",")[0].strip()
            if tgt:
                edges.append((src, tgt))

    elif ext in {".js", ".ts", ".jsx", ".tsx"}:
        for m in _JS_IMPORT_RE.finditer(content):
            tgt = m.group(1) or m.group(2) or ""
            if tgt:
                edges.append((src, tgt))

    return edges


def _parse_dependencies(files: list[_FileInfo]) -> dict[str, list[str]]:
    """Extract dependency lists from well-known manifest files."""
    by_name = {fi.path.name.lower(): fi for fi in files}
    deps: dict[str, list[str]] = {}

    fi = by_name.get("requirements.txt")
    if fi:
        try:
            lines = fi.path.read_text(encoding="utf-8", errors="replace").splitlines()
            deps["python"] = [
                re.split(r"[>=<!]", ln)[0].strip()
                for ln in lines
                if ln.strip() and not ln.startswith("#")
            ][:60]
        except OSError:
            pass

    fi = by_name.get("package.json")
    if fi:
        try:
            pkg = json.loads(fi.path.read_text(encoding="utf-8", errors="replace"))
            items = list(pkg.get("dependencies", {}).keys())
            items += list(pkg.get("devDependencies", {}).keys())
            deps["node"] = items[:60]
        except (OSError, json.JSONDecodeError):
            pass

    fi = by_name.get("pyproject.toml")
    if fi and "python" not in deps:
        try:
            content = fi.path.read_text(encoding="utf-8", errors="replace")
            matches = re.findall(r'"([a-zA-Z][a-zA-Z0-9_\-]+)[>=<!\[]', content)
            if matches:
                deps["python"] = matches[:60]
        except OSError:
            pass

    fi = by_name.get("cargo.toml")
    if fi:
        try:
            content = fi.path.read_text(encoding="utf-8", errors="replace")
            deps["rust"] = re.findall(r'^(\w[\w-]*)\s*=', content, re.MULTILINE)[:60]
        except OSError:
            pass

    fi = by_name.get("go.mod")
    if fi:
        try:
            content = fi.path.read_text(encoding="utf-8", errors="replace")
            deps["go"] = re.findall(r'^\s+([\w./\-]+)\s+v', content, re.MULTILINE)[:60]
        except OSError:
            pass

    return deps


def _arch_summary_via_llm(
    repo_name: str,
    lang_breakdown: dict[str, int],
    file_count: int,
    major_modules: list[str],
    import_edges: list[tuple[str, str]],
) -> str:
    """Generate a 2-3 sentence architecture summary using the local LLM."""
    lang_str = ", ".join(
        f"{lang}: {n}"
        for lang, n in sorted(lang_breakdown.items(), key=lambda x: -x[1])[:8]
    )
    mod_str = ", ".join(major_modules[:20]) or "N/A"
    edge_sample = "; ".join(
        f"{Path(s).name}→{t}" for s, t in import_edges[:10]
    )
    prompt = (
        f"Repository: {repo_name}\n"
        f"Languages: {lang_str}\n"
        f"Total files: {file_count}\n"
        f"Top-level modules: {mod_str}\n"
        f"Sample imports: {edge_sample}\n\n"
        "Write a 2-3 sentence technical architecture summary."
    )
    try:
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": "You are a software architecture analyst. Be concise and technical."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.1,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return (
            f"{repo_name} is a {file_count}-file repository primarily written in "
            f"{lang_str}. Key modules: {mod_str}."
        )


# ─── Public API ──────────────────────────────────────────────────────────────


def do_recursive_semantic_ingest(
    path: str,
    store: VectorStore,
    recursive: bool = True,
    include_patterns: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
    max_files: int = 5000,
    summarize: bool = True,
    store_raw_metadata: bool = True,
) -> dict:
    """
    Scan a directory tree and ingest every text file into semantic memory.

    Pipeline:
      1. Scan (metadata only, no I/O)
      2. Deduplicate against already-ingested paths
      3. Parallel read
      4. Summarise (LLM) or truncate (quick mode)
      5. Batch embed + batch store
    """
    t0 = time.perf_counter()

    root = Path(path).resolve()
    if not root.exists():
        return {"status": "error", "error": f"Path not found: {path}"}
    if not root.is_dir():
        return {"status": "error", "error": f"Not a directory: {path}"}

    # Phase 1 — Scan
    all_files, scan_skipped = _scan_directory(
        root, recursive, include_patterns, exclude_patterns, max_files
    )
    files_scanned = len(all_files)

    # Phase 2 — Deduplicate
    already = store.get_ingested_paths()
    new_files = [fi for fi in all_files if str(fi.path) not in already]
    duplicates_removed = files_scanned - len(new_files)

    if not new_files:
        return {
            "status": "success",
            "files_scanned": files_scanned,
            "files_ingested": 0,
            "files_skipped": len(scan_skipped),
            "duplicates_removed": duplicates_removed,
            "time_seconds": round(time.perf_counter() - t0, 2),
            "memories_created": 0,
        }

    # Phase 3 — Parallel read
    errors: list[str] = []
    read_results: list[tuple[_FileInfo, str]] = []
    order = {fi.path: i for i, fi in enumerate(new_files)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_PARALLEL_READS) as pool:
        futs = {pool.submit(_read_file, fi): fi for fi in new_files}
        for fut in concurrent.futures.as_completed(futs):
            fi, content, err = fut.result()
            if err:
                errors.append(f"{fi.rel_path}: {err}")
            elif content is not None:
                read_results.append((fi, content))

    # Restore order after parallel scatter
    read_results.sort(key=lambda x: order.get(x[0].path, 0))

    if not read_results:
        return {
            "status": "success",
            "files_scanned": files_scanned,
            "files_ingested": 0,
            "files_skipped": len(scan_skipped) + len(errors),
            "duplicates_removed": duplicates_removed,
            "time_seconds": round(time.perf_counter() - t0, 2),
            "memories_created": 0,
            "errors": errors[:20],
        }

    # Phase 4 — Summarise or truncate
    summaries: list[tuple[_FileInfo, str]] = []
    for fi, content in read_results:
        if summarize:
            text = summarize_text(
                content[:_LLM_MAX_INPUT_CHARS],
                context_hint=f"File: {fi.rel_path}",
            )
        else:
            text = content[:_QUICK_SUMMARY_CHARS].strip() or f"[empty] {fi.rel_path}"
        if not text:
            text = f"[empty] {fi.rel_path}"
        summaries.append((fi, text))

    # Phase 5 — Build MemoryObjects and batch-store
    ts = datetime.now(timezone.utc)
    repo_root = str(root)
    memories: list[MemoryObject] = []
    for fi, text in summaries:
        tag_list = fi.tags(repo_root)
        if store_raw_metadata:
            tag_list.extend([f"size:{fi.size_bytes}", f"ext:{fi.extension.lstrip('.')}"])
        memories.append(MemoryObject(type="doc", content=text, timestamp=ts, tags=tag_list))

    stored_ids = store.store_batch(memories, batch_size=_BATCH_EMBED_SIZE)

    # Track ingestion
    store.mark_files_ingested_batch(
        [(str(summaries[i][0].path), sid) for i, sid in enumerate(stored_ids)]
    )

    return {
        "status": "success",
        "files_scanned": files_scanned,
        "files_ingested": len(stored_ids),
        "files_skipped": len(scan_skipped) + len(errors),
        "duplicates_removed": duplicates_removed,
        "time_seconds": round(time.perf_counter() - t0, 2),
        "memories_created": len(stored_ids),
        "errors": errors[:20],
    }


def do_bulk_read(paths: list[str]) -> dict:
    """
    Read multiple files in parallel and return ordered content.

    Outperforms N sequential reads because I/O waits are overlapped.
    Ordering is always preserved (matches input list order).
    """
    t0 = time.perf_counter()
    if not paths:
        return {"status": "success", "paths_requested": 0, "paths_read": 0,
                "time_seconds": 0.0, "results": []}

    path_objs = [Path(p) for p in paths]
    order = {p: i for i, p in enumerate(path_objs)}

    def _read(p: Path) -> tuple[Path, Optional[str], Optional[str]]:
        if not p.exists():
            return p, None, "not_found"
        if _is_binary(p):
            return p, None, "binary"
        try:
            return p, p.read_text(encoding="utf-8", errors="replace"), None
        except OSError as exc:
            return p, None, str(exc)

    raw: list[tuple[int, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_PARALLEL_READS) as pool:
        futs = {pool.submit(_read, p): p for p in path_objs}
        for fut in concurrent.futures.as_completed(futs):
            p, content, err = fut.result()
            raw.append((order[p], {
                "path": str(p),
                "content": content,
                "size_bytes": len(content.encode("utf-8")) if content else 0,
                "error": err,
            }))

    raw.sort(key=lambda x: x[0])
    results = [r for _, r in raw]

    return {
        "status": "success",
        "paths_requested": len(paths),
        "paths_read": sum(1 for r in results if r["error"] is None),
        "time_seconds": round(time.perf_counter() - t0, 4),
        "results": results,
    }


def do_semantic_snapshot_repo(path: str, store: VectorStore) -> dict:
    """
    Generate a semantic snapshot of an entire repository.

    Pipeline:
      1. Directory scan + language breakdown
      2. Important-file discovery + dependency parsing
      3. Parallel read + import-graph extraction
      4. Full ingestion (fast mode, no LLM summarisation)
      5. LLM architecture summary
      6. Store snapshot memory
    """
    t0 = time.perf_counter()

    root = Path(path).resolve()
    if not root.exists():
        return {"status": "error", "error": f"Path not found: {path}"}
    if not root.is_dir():
        return {"status": "error", "error": f"Not a directory: {path}"}

    repo_name = root.name

    # Phase 1 — Scan
    files, _ = _scan_directory(root, True, None, None, 5000)

    lang_breakdown: dict[str, int] = {}
    for fi in files:
        lang = _LANG_MAP.get(fi.extension, "Other")
        lang_breakdown[lang] = lang_breakdown.get(lang, 0) + 1

    important_files = [
        fi.rel_path for fi in files
        if fi.path.name.lower() in _IMPORTANT_FILENAMES
    ][:20]

    top_dirs: set[str] = set()
    for fi in files:
        parts = Path(fi.rel_path).parts
        if len(parts) > 1:
            top_dirs.add(parts[0])
    major_modules = sorted(top_dirs)[:30]

    total_size = sum(fi.size_bytes for fi in files)

    # Phase 2 — Dependencies
    deps = _parse_dependencies(files)

    # Phase 3 — Parallel read + import graph (first 1000 source files)
    source_files = [
        fi for fi in files if fi.extension in {".py", ".js", ".ts", ".jsx", ".tsx"}
    ][:1000]

    import_edges: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_PARALLEL_READS) as pool:
        futs = {pool.submit(_read_file, fi): fi for fi in source_files}
        for fut in concurrent.futures.as_completed(futs):
            fi, content, _ = fut.result()
            if content:
                import_edges.extend(_extract_imports(content, fi.path))

    import_edges = import_edges[:2000]

    # Phase 4 — Ingest (quick mode)
    ingest = do_recursive_semantic_ingest(
        path, store, recursive=True, summarize=False, store_raw_metadata=True
    )

    # Phase 5 — LLM architecture summary
    arch_summary = _arch_summary_via_llm(
        repo_name, lang_breakdown, len(files), major_modules, import_edges
    )

    # Phase 6 — Store snapshot memory
    snapshot_text = (
        f"Repository snapshot: {repo_name}\n\n"
        f"{arch_summary}\n\n"
        f"Languages: {json.dumps(lang_breakdown)}\n"
        f"Modules: {major_modules}\n"
        f"Dependencies: {list(deps.keys())}\n"
        f"Files: {len(files)}, Size: {total_size:,} bytes"
    )
    snapshot_memory = MemoryObject(
        type="doc",
        content=snapshot_text,
        tags=["snapshot", f"repo:{repo_name}", "architecture"],
    )
    snapshot_id = store.store(snapshot_memory)

    return {
        "status": "success",
        "repository_name": repo_name,
        "repository_path": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "language_breakdown": lang_breakdown,
        "file_count": len(files),
        "total_size_bytes": total_size,
        "major_modules": major_modules,
        "important_files": important_files,
        "dependencies": deps,
        "import_graph_edge_count": len(import_edges),
        "import_graph_sample": [
            {"from": s, "to": t} for s, t in import_edges[:50]
        ],
        "architecture_summary": arch_summary,
        "memories_created": ingest.get("memories_created", 0),
        "snapshot_memory_id": snapshot_id,
        "time_seconds": round(time.perf_counter() - t0, 2),
    }
