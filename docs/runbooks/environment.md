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
tests collected" even though tests exist (it also hides collection errors:
the real error shows up with the proxy). Retry the exact same command via
`rtk proxy pytest tests/ ...` before concluding anything is actually broken.
`python -m pytest ...` usually works too.

## Running the service locally

```bash
pip install -e ".[dev]"
SMOS_DATA_DIR=./.smos-data SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=... smos-server
```

First start needs the two admin variables (no default password exists). The embedding
model (sentence-transformers) downloads on first use. Stop the server when done.

## Leftover MCP registration (Governor's machine)

Before 0.2 SMOS was registered as a user-scope MCP server. That server no longer exists.
Remove the stale registration with:

```
claude mcp remove smos --scope user
```

The old `smos-mcp` pip distribution may also still be listed (`pip show smos-mcp`); the
editable install of this repo now registers as `smos` (`pip uninstall smos-mcp` removes
the old name).
