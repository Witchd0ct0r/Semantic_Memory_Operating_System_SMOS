"""A tiny local stand-in for the parts of GitHub the updater talks to (tests only)."""
from __future__ import annotations

import http.server
import json
import threading
from typing import Callable, Union

Response = Union[tuple, Callable[[str], tuple]]  # (status, headers dict, body bytes)


def json_response(obj, status: int = 200, headers: dict | None = None) -> tuple:
    return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode()


def release_tarball(sha: str, version: str, files: dict | None = None, repo: str = "repo") -> bytes:
    """A GitHub-style source archive: top directory <repo>-<sha>, containing a tiny importable smos package."""
    import io
    import tarfile

    members = {
        "smos/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "smos/api/__init__.py": b"",
        "smos/api/app.py": b"READY = True\n",
        "Dockerfile": b"FROM python:3.12-slim\n",
        **{k: (v if isinstance(v, bytes) else v.encode()) for k, v in (files or {}).items()},
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"{repo}-{sha}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


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
