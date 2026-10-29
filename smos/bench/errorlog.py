"""Collects the requests a benchmark run saw fail, grouped, so the report can say what failed and when."""
from __future__ import annotations

import json
import socket
import threading
from typing import Optional

MAX_TEXT = 200      # characters kept of any message
MAX_GROUPS = 50     # groups listed in the summary; the rest are counted


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
