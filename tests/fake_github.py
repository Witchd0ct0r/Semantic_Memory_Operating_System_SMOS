"""A tiny local stand-in for the parts of GitHub the updater talks to (tests only)."""
from __future__ import annotations

import http.server
import json
import threading
from typing import Callable, Union

Response = Union[tuple, Callable[[str], tuple]]  # (status, headers dict, body bytes)


def json_response(obj, status: int = 200, headers: dict | None = None) -> tuple:
    return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode()


class FakeGitHub:
    def __init__(self) -> None:
        self.routes: dict[str, Response] = {}
        self.requests: list[str] = []
        self._server: http.server.ThreadingHTTPServer | None = None

    def __enter__(self) -> "FakeGitHub":
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                owner.requests.append(self.path)
                route = owner.routes.get(self.path)
                if route is None:
                    status, headers, body = 404, {}, b'{"message": "Not Found"}'
                else:
                    status, headers, body = route(self.path) if callable(route) else route
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}"
