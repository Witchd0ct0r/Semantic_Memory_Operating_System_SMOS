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
