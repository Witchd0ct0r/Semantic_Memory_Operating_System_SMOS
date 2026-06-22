"""smos setup — interactive first-run installer."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_CLAUDE_MD_SNIPPET = """
<!-- smos: semantic memory operating system — file reading policy -->
## SMOS active — file reading policy

For any file you are NOT about to edit, use `tool_read_file_compress` instead of
the built-in Read tool. Pass the absolute path. The full file content will NOT
enter the context window — only a compressed summary is returned and stored.

Use the built-in Read tool ONLY immediately before an Edit or Write call.

Before reading any file, call `tool_semantic_query` first. If the query returns
useful context, skip the read entirely.

Storage routing:
- Prose, analysis, notes → `tool_semantic_store` (LLM-compressed, queryable)
- Code, diffs, exact text → `tool_store_verbatim` (lossless, retrieve by key)
<!-- end smos -->
"""

_MODELS = [
    ("qwen2.5:7b",  "Best quality  (~4.7 GB, needs 16 GB RAM)"),
    ("qwen2.5:3b",  "Good quality  (~2.0 GB, needs  8 GB RAM)"),
    ("qwen2.5:1.5b","Fast, lighter (~0.9 GB, needs  6 GB RAM)"),
    ("none",        "Extractive fallback only — no Ollama needed"),
]


def _ok(msg: str) -> None:
    print(f"  [ok] {msg}")


def _warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def _check_python() -> None:
    v = sys.version_info
    if v < (3, 10):
        _fail(f"Python 3.10+ required (found {v.major}.{v.minor})")
    _ok(f"Python {v.major}.{v.minor}.{v.micro}")


def _check_claude_cli() -> None:
    if shutil.which("claude") is None:
        _fail("Claude Code CLI not found. Install from https://claude.ai/code")
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    _ok(f"Claude Code {result.stdout.strip()}")


def _check_ollama() -> bool:
    if shutil.which("ollama") is None:
        _warn("Ollama not found. Install from https://ollama.com/download")
        _warn("Without Ollama, SMOS will use extractive fallback summarization.")
        return False
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/", timeout=3)
        _ok("Ollama running at http://localhost:11434")
        return True
    except Exception:
        _warn("Ollama installed but not running. Start it with: ollama serve")
        return False


def _install_python_deps() -> None:
    deps = [
        "mcp[cli]>=1.0.0,<2.0.0",
        "faiss-cpu>=1.8.0",
        "sentence-transformers>=3.0.0,<4.0.0",
        "openai>=1.50.0,<2.0.0",
        "pydantic>=2.5.0,<3.0.0",
        "numpy>=1.26.0",
    ]
    missing = []
    pkg_map = {
        "mcp": "mcp",
        "faiss": "faiss",
        "sentence_transformers": "sentence-transformers",
        "openai": "openai",
        "pydantic": "pydantic",
        "numpy": "numpy",
    }
    for mod, pkg in pkg_map.items():
        if importlib.util.find_spec(mod) is None:
            missing.append(pkg)

    if not missing:
        _ok("Python dependencies already installed")
        return

    print(f"  Installing: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet"] + deps)
    _ok("Python dependencies installed")


def _choose_model() -> str:
    print()
    print("  Choose summarization model:")
    for i, (name, desc) in enumerate(_MODELS, 1):
        print(f"    [{i}] {name:<16} — {desc}")
    while True:
        choice = input("  Selection [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(_MODELS):
            return _MODELS[int(choice) - 1][0]
        print("  Invalid choice. Enter a number 1–4.")


def _pull_model(model: str, ollama_ok: bool) -> None:
    if model == "none":
        _ok("Skipping model download — extractive fallback mode")
        return
    if not ollama_ok:
        _warn(f"Ollama not running — skipping {model} pull. Run 'ollama pull {model}' later.")
        return

    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if model in result.stdout:
        _ok(f"{model} already available")
        return

    print(f"  Pulling {model} (this may take several minutes)...")
    subprocess.run(["ollama", "pull", model], check=True)
    _ok(f"{model} ready")


def _register_mcp(dry_run: bool) -> None:
    cmd = ["claude", "mcp", "add", "smos", "--scope", "user", "--", "smos-server"]
    if dry_run:
        print(f"  Would run: {' '.join(cmd)}")
        return
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in result.stderr:
        _warn(f"claude mcp add returned: {result.stderr.strip()}")
    else:
        _ok("Registered: smos MCP server (scope: user)")


def _write_claude_md(dry_run: bool) -> None:
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if dry_run:
        print(f"  Would append SMOS policy to {claude_md}")
        print(_CLAUDE_MD_SNIPPET)
        return

    print(f"\n  The following will be appended to {claude_md}:")
    print(_CLAUDE_MD_SNIPPET)
    answer = input("  Append? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        with open(claude_md, "a", encoding="utf-8") as f:
            f.write(_CLAUDE_MD_SNIPPET)
        _ok(f"Written to {claude_md}")
    else:
        _warn("Skipped. Claude may not follow the correct file-reading policy without this.")


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    print("SMOS Setup")
    print("=" * 40)

    print("\nChecking prerequisites...")
    _check_python()
    _check_claude_cli()
    ollama_ok = _check_ollama()

    print("\nInstalling Python dependencies...")
    _install_python_deps()

    print("\nConfiguring local LLM...")
    model = _choose_model()
    _pull_model(model, ollama_ok)

    if model != "none":
        env_path = Path.home() / ".smos" / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(f"OLLAMA_MODEL={model}\n", encoding="utf-8")
        _ok(f"Model saved to {env_path}")

    print("\nRegistering with Claude Code...")
    _register_mcp(dry_run)

    print("\nBehavioural instructions...")
    _write_claude_md(dry_run)

    data_dir = Path.home() / ".smos" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 40)
    print("Done. Restart Claude Code to activate SMOS.")
    print(f"Data directory: {data_dir}")
    print()
    print("Verify with: claude mcp list")
