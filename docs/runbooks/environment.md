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

## No `smos` MCP server is registered on this machine (as of 2026-07-27)

`claude mcp list` does not show a `smos` server — despite this repo's own
CLAUDE.md and `smos/install/cli.py` describing a `claude mcp add smos
--scope user -- smos-server` registration step, that step has not been run
(or was undone) on this machine. Don't assume a live SMOS MCP connection
exists; verify with `claude mcp list` before writing a plan that depends on
one (a headless `claude -p` subprocess connecting to it, an in-session
`mcp__smos__*` tool call, etc.).

If you need the real retrieval mechanism without the MCP layer, call the
underlying functions directly instead: `smos.tools.semantic_tools.semantic_query`,
`smos.memory.recall_engine.recall`, `smos.memory.exact_retrieval.reconstruct`,
each bound to a `VectorStore(persist_path=...)` — these are exactly what
`tool_semantic_query`/`tool_recall`/`tool_reconstruct` thinly wrap in
`smos/server.py`. See `sandbox/toolchain-benchmark/scripts/smos_tools.py`
(from the 2026-07-27 toolchain benchmark) for a working example.

To actually install it: run `python -m smos.install.cli` (or however this
repo's installer is invoked — check `smos/install/cli.py`'s `_setup()`) —
this registers the MCP server at **user scope**, affecting every Claude
Code project on this machine, not just this repo. Confirm with the user
before running it.
