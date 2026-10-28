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

## Docker on the Windows development PC

Docker Desktop is installed but its engine is **off by default**. Start and stop it from the shell:

```
docker desktop start      # then wait: docker info --format '{{.ServerVersion}}' succeeds
docker desktop stop       # always stop it when the work is done
```

- The first build of the image takes about 8 minutes (3 GB download); later builds reuse cached layers unless
  `pyproject.toml` or the Dockerfile's early steps change. Docker Desktop reports the image as ~9.8 GB (layers
  counted twice); on Linux it is about 6 GB.
- In Git Bash, set `MSYS_NO_PATHCONV=1` before `docker run -v ... /container/path` and `-e SOME_PATH=/...`, otherwise
  `/opt/...` is rewritten to `C:/Program Files/Git/opt/...`. Use `$(pwd -W)` for host paths in `-v`.
- `docker exec` needs `-i` to receive a heredoc on stdin.
- A temporary build context must live inside the repo (for example `.superpowers/ctx-x`, which is git-ignored);
  `mktemp -d` paths (`/tmp/...`) are not visible to Docker Desktop.
- Test containers I start use names like `smos-offline`, `smos-dash`; remove them and delete the throwaway `.env`
  afterwards (`.env` is git-ignored but holds a test password).

## Shell quirks that cost time

- Very large `bash` commands that mix several heredocs fail with `unexpected EOF`; write files with the Write tool
  and keep shell commands short.
- A bare `pytest` through the `rtk` hook may print "No tests collected" and hides collection errors; use
  `rtk proxy pytest ...` or `python -m pytest ...`.
- Chaining `git commit` after `pytest ... | tail -1` commits even when tests failed (`tail` succeeds). Capture the
  summary line and test it before committing.
- The browser automation tool cannot open `file://` URLs; serve a saved page with `python -m http.server` on
  127.0.0.1 and stop the server afterwards.
