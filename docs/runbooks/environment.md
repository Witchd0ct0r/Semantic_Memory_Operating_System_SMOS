# Environment notes

## Python: never invoke `python3` — use `python` or `pytest`

On this machine, `python3` resolves to the Windows Store app-execution-alias
stub (`AppData/Local/Microsoft/WindowsApps/python3`), not the real
interpreter — running it pops up the Microsoft Store on screen instead of
executing anything. The real interpreter is a miniconda install and only
resolves correctly under the names `python` and `pytest` (both directly on
`PATH`, no env activation needed — `$CONDA_PREFIX` is empty even when this
works):

```
$ which python   # -> /c/Users/<user>/miniconda3/python        (real)
$ which python3  # -> .../WindowsApps/python3                  (Store stub)
$ which pytest   # -> /c/Users/<user>/miniconda3/Scripts/pytest (real)
```

Always use `pytest ...` or `python -m pytest ...` — never `python3`. If a
subagent hits this, it should never attempt to "fix" it by installing a
Python version manager or anything else; the fix is simply to stop using
`python3`.

## pytest "No tests collected"

A local `rtk` hook occasionally makes a bare `pytest tests/ ...` report "No
tests collected" even though tests exist. Retry the exact same command via
`rtk proxy pytest tests/ ...` before concluding anything is actually broken.

## No `smos` MCP server is registered on this machine (as of 2026-07-28)

`claude mcp list` does not show a `smos` server. Don't assume a live SMOS MCP
connection exists; verify with `claude mcp list` before writing a plan that
depends on one (a headless `claude -p` subprocess connecting to it, an
in-session `mcp__smos__*` tool call, etc.).

If you need the real retrieval mechanism without the MCP layer, call the
underlying functions directly instead: `smos.tools.semantic_tools.semantic_query`,
`smos.memory.recall_engine.recall`, `smos.memory.exact_retrieval.reconstruct`,
each bound to a `VectorStore(persist_path=...)` — these are exactly what
`tool_semantic_query`/`tool_recall`/`tool_reconstruct` thinly wrap in
`smos/server.py`. See `sandbox/toolchain-benchmark/scripts/smos_tools.py`
(from the 2026-07-27 toolchain benchmark) for a working example.

**The pip install on this machine was found corrupted (2026-07-28):**
`pip show smos-mcp` reports "not found," but the `smos`/`smos-server`
console scripts still exist on PATH — site-packages had orphaned
`~mos_mcp-*.dist-info` directories (pip's tilde-rename safety net from an
interrupted uninstall on Windows, never rolled back or finalized). Its
`direct_url.json` also showed it was installed via
`pip install git+https://github.com/.../SMOS.git` pinned at the same stale
commit as `origin/main` — this repo's remote has sat dozens of commits
behind local `main` for months, so that install method can never pick up
local work. `smos/install/cli.py` was revised to catch and fix this class
of problem (see `a7464e9`): `_check_smos_package()` now fails loudly on a
missing/orphaned dist-info instead of silently proceeding, and `_update()`
reinstalls editable from a local git checkout (`pip install -e .`) instead
of the GitHub remote when one is detected.

**To actually install/register on this machine:** don't just run `smos
setup` blindly — first confirm `pip show smos-mcp` isn't reporting a
corrupted/orphaned state (fix with `pip install -e .` from this repo root
if so), then run `smos setup --skip-claude-md` (this repo's own `CLAUDE.md`
already carries the SMOS policy directly — see the "SMOS active" section —
so the global `~/.claude/CLAUDE.md` write would be redundant). Registration
is `claude mcp add smos --scope user -- smos-server` under the hood — this
affects **every** Claude Code project on this machine, not just this repo.
Confirm with the user before running it.

## Ingesting this repo's own code into SMOS (no MCP server needed)

`docs/runbooks/scripts/ingest_code.py` calls `do_recursive_semantic_ingest()`
directly (same no-MCP-layer workaround as above), restricted to code
extensions only (`include_patterns`) so it never overlaps with
`.claude/skills/smos-doc-sync/`'s document-only ingestion path. Run with
`python docs/runbooks/scripts/ingest_code.py`. With `summarize=True` (LLM
compression via Ollama) it's slow on this machine — 97 code files took
~33 minutes (~1990s) CPU-bound, well past the README's GPU-accelerated
benchmark numbers — expect it to run long and let it finish rather than
assume it hung.
