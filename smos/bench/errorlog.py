"""Collects the requests a benchmark run saw fail, grouped, so the report can say what failed and when."""
from __future__ import annotations

import json
import re
import socket
import threading
from pathlib import Path
from typing import Optional

MAX_TEXT = 200      # characters kept of any message
MAX_GROUPS = 50     # groups listed in the summary; the rest are counted
MAX_SERVER_EXCEPTIONS = 20  # distinct server-side exception lines kept


_GROUP_BAR = re.compile(r"^\s*\|\s?(.*)$")      # a line inside an exception-group block: "  | text"
_GROUP_END = re.compile(r"^\s*\+-{10,}\s*$")    # the line that closes a group: "  +------------"
_GROUP_MEMBER = re.compile(r"^\s*\+(?:-\+)?-+ (\d+) -+\s*$")  # starts member N of a group: "  +----- 2 -----"


def _clean(text: str) -> str:
    return " ".join(text.split())[:MAX_TEXT]


def category_for(status: Optional[int], error: Optional[BaseException]) -> str:
    """`http 500` for an answer, `timeout` for a timed-out request, otherwise the exception's class name."""
    if error is not None:
        return "timeout" if isinstance(error, (TimeoutError, socket.timeout)) else type(error).__name__
    return f"http {status}"


def message_from_body(data: bytes) -> str:
    """The API's error text (JSON `detail` or `message`) or else the start of the body; never more than MAX_TEXT."""
    text = data[: MAX_TEXT * 8].decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return _clean(text)
    if isinstance(parsed, dict):
        for key in ("detail", "message"):
            if key in parsed:
                value = parsed[key]
                return _clean(value if isinstance(value, str) else json.dumps(value))
    return _clean(text)


class ErrorLog:
    """Thread-safe. Keeps counts per (phase, request kind, category) and the first message seen for each group."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._groups: dict[tuple[str, str, str], dict] = {}
        self._total = 0
        self._server: dict = {"exceptions": {}, "locked_lines": 0, "timeout_lines": 0}

    def record(self, phase: str, request: str, category: str, message: str = "", elapsed_s: float = 0.0) -> None:
        with self._lock:
            self._total += 1
            group = self._groups.get((phase, request, category))
            if group is None:
                self._groups[(phase, request, category)] = {
                    "phase": phase, "request": request, "category": category, "count": 1,
                    "first_s": elapsed_s, "last_s": elapsed_s, "example": _clean(message),
                }
            else:
                group["count"] += 1
                group["first_s"] = min(group["first_s"], elapsed_s)
                group["last_s"] = max(group["last_s"], elapsed_s)

    def add_server_log(self, path: Path) -> None:
        """Merge a digest of a (finished) throwaway server's log: the root exception line of each traceback, counted,
        plus how many lines mention a locked database or a timeout. Frames, paths and request lines are not kept."""
        exceptions: dict[str, int] = {}
        locked = timeouts = 0
        expecting_exception = skip_block = False
        depth = 0  # exception groups can nest; 0 means not inside one
        leaf = ""

        def count(text: str) -> None:
            key = _clean(text)
            if key in exceptions or len(exceptions) < MAX_SERVER_EXCEPTIONS:
                exceptions[key] = exceptions.get(key, 0) + 1
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    line = raw.rstrip("\r\n")
                    lowered = line.lower()
                    locked += "database is locked" in lowered
                    timeouts += "timed out" in lowered or "timeout" in lowered
                    if depth and line and not line[0].isspace():
                        depth, leaf = 0, ""  # a group block is indented; a flush-left line means it ended (or was cut off)
                    if "Exception Group Traceback (most recent call last):" in line:
                        if depth:
                            depth += 1
                        elif not skip_block:
                            depth = 1
                        skip_block = False
                        leaf, expecting_exception = "", False
                    elif depth:
                        # Python 3.11+ prints grouped errors as "  | ..." blocks. Each member's last bar line is the
                        # exception that was raised; lines naming a group that wrapped them are dropped.
                        member = _GROUP_MEMBER.match(line)
                        if member:
                            if int(member.group(1)) > 1 and leaf:
                                count(leaf)
                            leaf = ""
                        elif _GROUP_END.match(line):
                            if leaf:
                                count(leaf)
                            leaf = ""
                            depth -= 1
                        else:
                            match = _GROUP_BAR.match(line)
                            text = match.group(1) if match else ""
                            if text and not text[0].isspace() and not text.startswith(("Traceback", "During handling",
                                                                                       "The above exception")):
                                leaf = text
                    elif line.startswith("Traceback (most recent call last):"):
                        expecting_exception, skip_block = not skip_block, False
                    elif line.startswith(("During handling of the above exception", "The above exception was the direct cause")):
                        skip_block = True  # the next traceback is a consequence; the root was already counted
                    elif expecting_exception and line and not line[0].isspace():
                        key = _clean(line)
                        if key in exceptions or len(exceptions) < MAX_SERVER_EXCEPTIONS:
                            exceptions[key] = exceptions.get(key, 0) + 1
                        expecting_exception = False
        except OSError:
            return
        with self._lock:
            merged = self._server["exceptions"]
            for key, times in exceptions.items():
                if key in merged or len(merged) < MAX_SERVER_EXCEPTIONS:
                    merged[key] = merged.get(key, 0) + times
            self._server["locked_lines"] += locked
            self._server["timeout_lines"] += timeouts

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def summary(self) -> dict:
        with self._lock:
            ordered = sorted(self._groups.values(), key=lambda g: (-g["count"], g["first_s"]))
            return {
                "total": self._total,
                "groups": [dict(g) for g in ordered[:MAX_GROUPS]],
                "more_groups": max(0, len(ordered) - MAX_GROUPS),
                "server": {
                    "exceptions": [{"error": e, "count": c} for e, c in
                                   sorted(self._server["exceptions"].items(), key=lambda kv: -kv[1])],
                    "locked_lines": self._server["locked_lines"],
                    "timeout_lines": self._server["timeout_lines"],
                },
            }
