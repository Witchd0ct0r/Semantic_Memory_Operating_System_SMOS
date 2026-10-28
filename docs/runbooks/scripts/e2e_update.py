"""Real end-to-end run of the self-updater: real supervisor, real uvicorn child, local fake GitHub.

Not part of the pytest suite. Run: python docs/runbooks/scripts/e2e_update.py   (about 12 minutes; Windows: stops servers with taskkill)
"""
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from smos.admin import tokens  # noqa: E402
from smos.admin.db import open_admin_db  # noqa: E402
from smos.update import manifest as mf  # noqa: E402
from tests.fake_github import FakeGitHub, json_response  # noqa: E402

PW = "correct-horse-1"
REPO_SLUG = "o/r"
SHAS = {"9.0.0": "a" * 40, "9.0.1": "b" * 40, "9.0.2": "c" * 40, "9.0.3": "d" * 40}
results: list[tuple[bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    results.append((bool(condition), label))
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""), flush=True)


def tarball(version: str, patches: dict | None = None) -> bytes:
    sha = SHAS[version]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted((REPO / "smos").rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            data = path.read_bytes()
            if rel == "smos/__init__.py":
                data = re.sub(rb'__version__ = "[^"]+"', f'__version__ = "{version}"'.encode(), data)
            for patch_rel, patch in (patches or {}).items():
                if rel == patch_rel:
                    patched = patch(data)
                    assert patched != data, f"patch for {rel} did nothing"
                    data = patched
            info = tarfile.TarInfo(f"repo-{sha}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def publish(fake: FakeGitHub, version: str, patches=None, manifest_override: dict | None = None) -> None:
    fake.routes.clear()
    tag, sha = f"v{version}", SHAS[version]
    fake.routes[f"/repos/{REPO_SLUG}/releases/latest"] = json_response(
        {"tag_name": tag, "body": f"Release notes for {version}\n<b>not html</b>",
         "html_url": f"https://github.com/{REPO_SLUG}/releases/tag/{tag}", "draft": False, "prerelease": False})
    fake.routes[f"/repos/{REPO_SLUG}/commits/{tag}"] = json_response({"sha": sha})
    ids = dict(IMAGE_MANIFEST)
    ids.update(manifest_override or {})
    root = [{"path": k, "type": "tree" if k == "models" else "blob", "sha": v} for k, v in ids.items() if "/" not in k]
    root.append({"path": "smos", "type": "tree", "sha": "5" * 40})
    fake.routes[f"/repos/{REPO_SLUG}/git/trees/{sha}"] = json_response({"tree": root})
    fake.routes[f"/repos/{REPO_SLUG}/git/trees/{'5' * 40}"] = json_response(
        {"tree": [{"path": "launcher.py", "type": "blob", "sha": ids["smos/launcher.py"]}]})
    fake.routes[f"/{REPO_SLUG}/archive/{sha}.tar.gz"] = (200, {}, tarball(version, patches))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, work: Path, data: Path, info: Path, fake: FakeGitHub, port: int) -> None:
        self.work, self.data, self.info, self.fake, self.port = work, data, info, fake, port
        self.proc = None
        self.log = work / "server.log"
        self.base = f"http://127.0.0.1:{port}"

    def start(self) -> None:
        env = {**os.environ,
               "SMOS_DATA_DIR": str(self.data), "SMOS_PORT": str(self.port), "SMOS_HOST": "127.0.0.1",
               "SMOS_BUILD_INFO": str(self.info), "SMOS_UPDATE_REPO": REPO_SLUG,
               "SMOS_UPDATE_API_BASE": self.fake.url, "SMOS_UPDATE_ARCHIVE_BASE": self.fake.url,
               "SMOS_EMBEDDING_MODEL_DIR": str(REPO / "models" / "all-MiniLM-L6-v2"),
               "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONPATH": str(REPO),
               "SMOS_ADMIN_USER": "admin", "SMOS_ADMIN_PASSWORD": PW, "PYTHONUNBUFFERED": "1"}
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen([sys.executable, "-m", "smos.launcher"], cwd=self.work, env=env,
                                         stdout=out, stderr=subprocess.STDOUT)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            # Windows has no SIGTERM delivery: kill the whole tree (supervisor + its server child)
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            time.sleep(2)

    def health(self):
        try:
            r = httpx.get(f"{self.base}/api/v1/health", timeout=3, trust_env=False)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def wait_version(self, version: str, timeout: float = 240) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            h = self.health()
            if h and h.get("version") == version:
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(1)
        return False


def client(server: Server) -> httpx.Client:
    c = httpx.Client(base_url=server.base, timeout=60, trust_env=False)
    r = c.post("/admin/login", data={"username": "admin", "password": PW}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def csrf(c: httpx.Client) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/admin/dashboard").text).group(1)


def tail(path: Path, n: int = 25) -> str:
    return "\n".join(path.read_text(errors="replace").splitlines()[-n:])


def last_result(data: Path):
    p = data / "updates" / "last-result.json"
    return json.loads(p.read_text()) if p.exists() else None


def update_log(data: Path) -> list[tuple]:
    conn = sqlite3.connect(f"file:{data / 'admin.db'}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT outcome, admin_user, from_version, to_version FROM update_log ORDER BY id").fetchall()
    finally:
        conn.close()


def main() -> int:
    global IMAGE_MANIFEST
    work = Path(tempfile.mkdtemp(prefix="smos_e2e_"))
    data = work / "data"
    info = work / "build-info.json"
    import smos

    base_version = smos.__version__
    IMAGE_MANIFEST = mf.write_build_info(info, {p: REPO / p for p in mf.REBUILD_PATHS})["manifest"]
    conn = open_admin_db(data)
    _tid, token = tokens.create_token(conn, "e2e", scope="rw")
    conn.close()
    auth = {"Authorization": f"Bearer {token}"}
    print(f"work dir: {work}\nbaseline version: {base_version}", flush=True)

    server = None
    with FakeGitHub() as fake:
        server = Server(work, data, info, fake, free_port())
        try:
            # ------------------------------------------------ 1. baseline, no release yet
            server.start()
            check("1 baseline server starts and reports the baked version", server.wait_version(base_version, 240),
                  tail(server.log, 5))
            c = client(server)
            doc = {"external_handle": {"system": "e2e", "native_id": "E2E-1"},
                   "blocks": [{"block_type": "paragraph", "text": "Requirement E2E-1 limits radiated emissions."}]}
            check("1 ingest a document", c.post("/api/v1/documents", json=doc, headers=auth).status_code == 200)
            q = c.post("/api/v1/query", json={"query": "radiated emissions limits"}, headers=auth).json()
            check("1 query finds it", bool(q.get("sources")))
            c.post("/admin/update/check", data={"csrf": csrf(c)})
            page = c.get("/admin/dashboard")
            check("1 repository without releases: dashboard 200 and 'up to date' (no error)",
                  page.status_code == 200 and "SMOS is up to date" in page.text)

            # ------------------------------------------------ 2. apply a good release
            publish(fake, "9.0.0")
            time.sleep(61)  # "Check now" is limited to once a minute
            c.post("/admin/update/check", data={"csrf": csrf(c)})
            page = c.get("/admin/dashboard").text
            check("2 banner offers release 9.0.0", "Release 9.0.0 is available" in page)
            upd = c.get("/admin/update").text
            check("2 confirmation page shows tag, commit and escaped notes",
                  "v9.0.0" in upd and SHAS["9.0.0"] in upd and "&lt;b&gt;not html&lt;/b&gt;" in upd)
            r = c.post("/admin/update/apply", data={"csrf": csrf(c), "version": "9.0.0"}, follow_redirects=False)
            check("2 apply is accepted", r.status_code == 303, f"{r.status_code} {r.headers.get('location')}")
            saw_503 = False
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    w = httpx.post(f"{server.base}/api/v1/memories", json={"type": "doc", "content": "write during update"},
                                   headers=auth, timeout=3, trust_env=False)
                    if w.status_code == 503:
                        saw_503 = True
                        check("2 writes are refused with 503 + Retry-After during the update window",
                              w.headers.get("retry-after") == "30")
                        break
                except Exception:
                    break  # already restarting
                time.sleep(0.05)
            if not saw_503:
                check("2 observed the 503 window", False, "window missed (it is short); see launcher log")
            check("2 new version comes up healthy", server.wait_version("9.0.0", 300), tail(server.log))
            check("2 supervisor process survived (no container restart needed)", server.proc.poll() is None)
            c2 = httpx.Client(base_url=server.base, timeout=60, trust_env=False, cookies=c.cookies)
            check("2 admin session still valid after the restart", c2.get("/admin/dashboard").status_code == 200)
            q = c2.post("/api/v1/query", json={"query": "radiated emissions limits"}, headers=auth).json()
            check("2 data intact after the update", bool(q.get("sources")))
            cur = json.loads((data / "updates" / "current.json").read_text())
            check("2 current.json points at 9.0.0 with the commit", cur["version"] == "9.0.0" and cur["sha"] == SHAS["9.0.0"])
            res = last_result(data)
            check("2 last-result ok", bool(res and res["ok"] and res["to_version"] == "9.0.0"), str(res))
            check("2 dashboard shows the result", "applied" in c2.get("/admin/dashboard").text)
            check("2 audit row 'applied' with the admin name",
                  ("applied", "admin", base_version, "9.0.0") in update_log(data), str(update_log(data)))
            check("2 snapshot of the old data exists", (data / "updates" / "snapshot" / "metadata.db").exists())

            # ------------------------------------------------ 3. release that does not import is refused up front
            publish(fake, "9.0.2", patches={"smos/api/app.py": lambda b: b + b"\ndef broken(:\n"})
            time.sleep(61)
            c2.post("/admin/update/check", data={"csrf": csrf(c2)})
            check("3 broken release is offered (the break is only found when it is test-imported)",
                  "Release 9.0.2 is available" in c2.get("/admin/dashboard").text)
            r = c2.post("/admin/update/apply", data={"csrf": csrf(c2), "version": "9.0.2"})
            check("3 refused at the import smoke check (409, nothing changed)",
                  r.status_code == 409 and "does not import" in r.text, f"{r.status_code}")
            check("3 server still on 9.0.0, no pending update, staging cleaned",
                  (server.health() or {}).get("version") == "9.0.0"
                  and not (data / "updates" / "pending.json").exists()
                  and not list((data / "updates").glob("staging-*")))
            check("3 write still works (no maintenance mode left behind)",
                  httpx.post(f"{server.base}/api/v1/memories", json={"type": "doc", "content": "still writable"},
                             headers=auth, timeout=10, trust_env=False).status_code == 200)

            # ------------------------------------------------ 4. restart: overlay persists, cooldown reset
            server.stop()
            server.start()
            check("4 after a supervisor restart the overlay still runs 9.0.0", server.wait_version("9.0.0", 240),
                  tail(server.log, 5))
            c3 = client(server)

            # ------------------------------------------------ 5. release that crashes and corrupts data -> rollback
            publish(fake, "9.0.1", patches={"smos/api/main.py": lambda b: re.sub(
                rb"def run\(\) -> None:\r?\n",
                b"def run() -> None:\n    import os as _o\n"
                b"    open(_o.path.join(_o.environ['SMOS_DATA_DIR'], 'metadata.db'), 'ab').write(b'GARBAGE' * 1000)\n"
                b"    raise RuntimeError('e2e: simulated broken release')\n", b, count=1)})
            before = (last_result(data) or {}).get("finished_at")
            time.sleep(61)
            c3.post("/admin/update/check", data={"csrf": csrf(c3)})
            check("5 release 9.0.1 offered", "Release 9.0.1 is available" in c3.get("/admin/dashboard").text)
            r = c3.post("/admin/update/apply", data={"csrf": csrf(c3), "version": "9.0.1"}, follow_redirects=False)
            check("5 apply accepted", r.status_code == 303)
            deadline = time.time() + 300
            while time.time() < deadline:
                res = last_result(data)
                if res and res.get("finished_at") != before and server.health():
                    break
                time.sleep(1)
            res = last_result(data)
            check("5 rollback recorded: ok=false with the child's output", bool(
                res and res["ok"] is False and "exited" in res["error"]
                and any("simulated broken release" in line for line in res["log_tail"])), str(res)[:300])
            check("5 old version is serving again", (server.health() or {}).get("version") == "9.0.0")
            blob = (data / "metadata.db").read_bytes()
            check("5 data restored (the corruption is gone)", b"GARBAGE" not in blob)
            c4 = client(server)
            q = c4.post("/api/v1/query", json={"query": "radiated emissions limits"}, headers=auth).json()
            check("5 query still finds the document after rollback", bool(q.get("sources")))
            cur = json.loads((data / "updates" / "current.json").read_text())
            check("5 code pointer back on 9.0.0, failed overlay removed",
                  cur["version"] == "9.0.0" and not (data / "updates" / "overlay" / "9.0.1").exists()
                  and not (data / "updates" / "pending.json").exists())
            text = c4.get("/admin/dashboard").text
            check("5 dashboard says rolled back and why", "rolled back" in text)
            check("5 audit row 'rolled_back' recorded (written by the new process)",
                  any(row[0] == "rolled_back" and row[1] == "admin" for row in update_log(data)), str(update_log(data)))

            # ------------------------------------------------ 6. release that needs a rebuild
            publish(fake, "9.0.3", manifest_override={"Dockerfile": "f" * 40})
            time.sleep(61)
            c4.post("/admin/update/check", data={"csrf": csrf(c4)})
            text = c4.get("/admin/dashboard").text
            check("6 banner says the release needs a rebuilt image and names the file",
                  "needs a rebuilt image" in text and "Dockerfile" in text)
            r = c4.post("/admin/update/apply", data={"csrf": csrf(c4), "version": "9.0.3"})
            check("6 apply refused (409) and nothing downloaded",
                  r.status_code == 409 and "rebuilt image" in r.text
                  and not any("archive" in p for p in fake.requests[-3:]))
        finally:
            if server is not None:
                server.stop()
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed; work dir {work}")
    for label in failed:
        print("FAILED:", label)
    return 1 if failed else 0


IMAGE_MANIFEST: dict = {}

if __name__ == "__main__":
    sys.exit(main())
