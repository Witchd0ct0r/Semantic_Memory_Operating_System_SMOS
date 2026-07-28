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

## `smos` MCP server IS registered on this machine (fixed 2026-07-28)

`claude mcp list` shows `smos: smos-server — ✔ Connected`, registered at
**user scope** (affects every Claude Code project on this machine, not just
this repo). Restart Claude Code if a session was already open when this was
set up, so it picks up the new MCP connection.

**What had to be fixed to get here, in case it regresses:** the pip install
on this machine was found corrupted — `pip show smos-mcp` reported "not
found" while the `smos`/`smos-server` console scripts still existed on PATH.
Site-packages had orphaned `~mos_mcp-*.dist-info` directories (pip's
tilde-rename safety net from an interrupted uninstall on Windows, never
rolled back or finalized). The orphaned dist-info's `direct_url.json` also
showed the prior install came from
`pip install git+https://github.com/.../SMOS.git` pinned at the same stale
commit as `origin/main` — this repo's remote has sat dozens of commits
behind local `main` for months, so that install method could never have
picked up local work even without the corruption.

Fix applied: removed the orphaned `~mos*` directories from
`miniconda3/Lib/site-packages`, then `pip install -e .` from this repo root
(confirmed via `pip show smos-mcp` → `Editable project location:
C:\private\semantic_memory`, and `direct_url.json` → `{"dir_info":
{"editable": true}, "url": "file:///C:/private/semantic_memory"}` — no more
GitHub/commit pin), then `claude mcp add smos --scope user -- smos-server`.
The global `~/.claude/CLAUDE.md` was deliberately left untouched (this
repo's own `CLAUDE.md` already carries the SMOS policy directly — see its
"SMOS active" section) — `smos/install/cli.py`'s `_setup()` was never
invoked; the three steps above were run directly instead, since `_setup()`
also has an interactive model-choice prompt that would hang under a
non-interactive shell. `smos/install/cli.py` was separately revised (see
`a7464e9`) so future installs/updates on this repo don't reintroduce this
class of problem: `_check_smos_package()` now fails loudly on a
missing/orphaned dist-info instead of silently proceeding, `_update()`
reinstalls editable from a local git checkout instead of the GitHub remote
when one is detected, and `smos setup --skip-claude-md` exists for exactly
this repo's situation.

If the registration ever needs to be redone: verify `pip show smos-mcp`
isn't reporting a corrupted/orphaned state first, fix with `pip install -e .`
from this repo root if so, then `claude mcp add smos --scope user --
smos-server`. If you need the real retrieval mechanism without the MCP
layer at all (e.g. scripting, or before registration exists), call the
underlying functions directly instead: `smos.tools.semantic_tools.semantic_query`,
`smos.memory.recall_engine.recall`, `smos.memory.exact_retrieval.reconstruct`,
each bound to a `VectorStore(persist_path=...)` — these are exactly what
`tool_semantic_query`/`tool_recall`/`tool_reconstruct` thinly wrap in
`smos/server.py`. See `sandbox/toolchain-benchmark/scripts/smos_tools.py`
(from the 2026-07-27 toolchain benchmark) for a working example.

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
