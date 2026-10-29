"""The HTTP side of the benchmark: a loopback SMOS server of its own and a small load client (standard library only)."""
from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import psutil

from smos.admin import tokens
from smos.admin.db import open_admin_db
from smos.bench.corpus import CorpusBuilder, Probe
from smos.bench.stats import summarize_latencies

CLIENT_LIMITED_SHARE = 0.85   # above this share of one core the load generator, not SMOS, is the limit
_QUERY_K = 10
_TIMEOUT_S = 60.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Conn:
    """One keep-alive connection; reconnects after an error."""

    def __init__(self, base_url: str, token: Optional[str]) -> None:
        parts = urlsplit(base_url)
        self._host, self._port, self._token = parts.hostname or "127.0.0.1", parts.port or 80, token
        self._conn: Optional[http.client.HTTPConnection] = None

    def request(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, bytes, float]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = json.dumps(body).encode() if body is not None else None
        started = time.perf_counter()
        try:
            if self._conn is None:
                self._conn = http.client.HTTPConnection(self._host, self._port, timeout=_TIMEOUT_S)
            self._conn.request(method, path, body=payload, headers=headers)
            response = self._conn.getresponse()
            data = response.read()
            return response.status, data, (time.perf_counter() - started) * 1000
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def request(base_url: str, method: str, path: str, *, token: Optional[str] = None,
            body: Optional[dict] = None) -> tuple[int, bytes, float]:
    conn = _Conn(base_url, token)
    try:
        return conn.request(method, path, body)
    finally:
        conn.close()


class BenchServer:
    """A real `python -m smos.api.main` on 127.0.0.1 over a throwaway data directory, with its own admin and token."""

    def __init__(self, data_dir: Path, port: int) -> None:
        self.data_dir, self.port = Path(data_dir), port
        self.base_url = f"http://127.0.0.1:{port}"
        self._admin_password = secrets.token_urlsafe(16)
        self.token = ""
        self._process: Optional[subprocess.Popen] = None

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    def start(self, timeout: float = 120.0) -> float:
        """Start the server and return the seconds until its health check answered."""
        conn = open_admin_db(self.data_dir)
        try:
            _id, self.token = tokens.create_token(conn, "benchmark", "rw")
        finally:
            conn.close()
        env = dict(os.environ)
        env.pop("SMOS_BENCH_LAUNCH_SECRET", None)
        env.update({
            "SMOS_DATA_DIR": str(self.data_dir), "SMOS_HOST": "127.0.0.1", "SMOS_PORT": str(self.port),
            "SMOS_SAMPLE_INTERVAL_S": "3600", "SMOS_ADMIN_USER": "bench",
            "SMOS_ADMIN_PASSWORD": self._admin_password,
        })
        log = open(self.data_dir.parent / f"server-{self.port}.log", "ab")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        started = time.perf_counter()
        self._process = subprocess.Popen([sys.executable, "-m", "smos.api.main"], env=env, stdout=log, stderr=log,
                                         stdin=subprocess.DEVNULL, creationflags=flags)
        log.close()
        deadline = started + timeout
        while time.perf_counter() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"the benchmark server exited with code {self._process.returncode} while starting")
            try:
                status, _body, _ms = request(self.base_url, "GET", "/api/v1/health")
                if status == 200:
                    return time.perf_counter() - started
            except OSError:
                pass
            time.sleep(0.05)
        self.stop()
        raise RuntimeError(f"the benchmark server did not answer within {timeout:.0f} s")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            family = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            family = []
        for proc in family + [psutil.Process(process.pid)] if process.poll() is None else family:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
        _gone, alive = psutil.wait_procs(family, timeout=5)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _query_body(probe: Probe) -> dict:
    return {"query": probe.query, "k": _QUERY_K}


def quality_probes(base_url: str, token: str, probes: list[Probe]) -> dict:
    """hit@1, hit@10 and mean reciprocal rank over the needle probes (synthetic text)."""
    conn, ranks = _Conn(base_url, token), []
    try:
        for probe in probes:
            if not probe.marker:
                continue
            status, data, _ms = conn.request("POST", "/api/v1/query", _query_body(probe))
            rank = None
            if status == 200:
                for position, item in enumerate(json.loads(data).get("results", []), start=1):
                    if probe.marker in item.get("content", ""):
                        rank = position
                        break
            ranks.append(rank)
    finally:
        conn.close()
    n = len(ranks)
    return {
        "n": n,
        "hit1": sum(1 for r in ranks if r == 1) / n if n else None,
        "hit10": sum(1 for r in ranks if r is not None and r <= 10) / n if n else None,
        "mrr": sum(1.0 / r for r in ranks if r) / n if n else None,
    }


def _small_document() -> dict:
    return {
        "external_handle": {"system": "bench-probe", "native_id": f"BP-{uuid.uuid4().hex[:10]}"},
        "doc_type": "requirements", "domain_tags": ["domain:quality"],
        "blocks": [{"block_type": "heading", "heading_level": 1, "text": "Probe document"},
                   {"block_type": "paragraph", "text": "The probe unit shall report its status within the limit."}],
    }


def latency_probe(base_url: str, token: str, probes: list[Probe], n: int, post_docs: int = 5) -> dict:
    """One client, one request at a time: the cold first query, n warm queries, node reads and small ingests.

    The small ingests add `post_docs` one-paragraph documents to the throwaway store.
    """
    conn = _Conn(base_url, token)
    doc_ids: list[str] = []
    try:
        status, data, cold_ms = conn.request("POST", "/api/v1/query", _query_body(probes[0]))
        queries = []
        for i in range(n):
            status, data, ms = conn.request("POST", "/api/v1/query", _query_body(probes[i % len(probes)]))
            if status == 200:
                queries.append(ms)
                for item in json.loads(data).get("results", []):
                    doc_id = (item.get("metadata") or {}).get("doc_id")
                    if doc_id and doc_id not in doc_ids:
                        doc_ids.append(doc_id)
        node_text = []
        for doc_id in doc_ids[:20]:
            status, _data, ms = conn.request("GET", f"/api/v1/nodes/{doc_id}/text")
            if status == 200:
                node_text.append(ms)
        post_doc = []
        for _ in range(post_docs):
            status, _data, ms = conn.request("POST", "/api/v1/documents", _small_document())
            if status == 200:
                post_doc.append(ms)
    finally:
        conn.close()
    return {"cold_ms": cold_ms, "query": summarize_latencies(queries),
            "node_text": summarize_latencies(node_text), "post_doc": summarize_latencies(post_doc)}


def _load(base_url: str, token: str, clients: int, seconds: float, probes: list[Probe],
          cpu_time: Callable[[], float], background: Optional[Callable[[threading.Event], None]] = None) -> dict:
    stop = threading.Event()
    results: list[list[tuple[float, bool]]] = [[] for _ in range(clients)]

    def worker(slot: int) -> None:
        conn, i = _Conn(base_url, token), slot
        try:
            while not stop.is_set():
                probe = probes[i % len(probes)]
                i += clients
                try:
                    status, _data, ms = conn.request("POST", "/api/v1/query", _query_body(probe))
                    results[slot].append((ms, 200 <= status < 300))
                except Exception:
                    results[slot].append((_TIMEOUT_S * 1000, False))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(slot,), daemon=True) for slot in range(clients)]
    extra = threading.Thread(target=background, args=(stop,), daemon=True) if background else None
    cpu_before, started = cpu_time(), time.perf_counter()
    for t in threads + ([extra] if extra else []):
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads + ([extra] if extra else []):
        t.join(timeout=_TIMEOUT_S + 5)
    elapsed = time.perf_counter() - started
    cpu_share = (cpu_time() - cpu_before) / elapsed if elapsed > 0 else 0.0

    everything = [r for per_client in results for r in per_client]
    ok = [ms for ms, good in everything if good]
    summary = summarize_latencies(ok)
    return {
        "clients": clients, "seconds": elapsed, "requests": len(everything), "errors": len(everything) - len(ok),
        "rps": len(ok) / elapsed if elapsed > 0 else 0.0,
        "p50_ms": summary["p50"], "p95_ms": summary["p95"], "p99_ms": summary["p99"],
        "client_cpu_share": cpu_share, "client_limited": cpu_share > CLIENT_LIMITED_SHARE,
    }


def run_step(base_url: str, token: str, clients: int, seconds: float, probes: list[Probe],
             cpu_time: Callable[[], float] = time.process_time) -> dict:
    """`clients` parallel clients querying for `seconds`; requests/s, latency percentiles, errors, client CPU share."""
    return _load(base_url, token, clients, seconds, probes, cpu_time)


def mixed_step(base_url: str, token: str, clients: int, seconds: float, probes: list[Probe],
               builder: CorpusBuilder, cpu_time: Callable[[], float] = time.process_time) -> dict:
    """The same read load twice: alone, then while one writer keeps ingesting new corpus documents."""
    alone = _load(base_url, token, clients, seconds, probes, cpu_time)
    written = {"docs": 0, "errors": 0}

    def writer(stop: threading.Event) -> None:
        conn = _Conn(base_url, token)
        try:
            while not stop.is_set():
                for doc in builder.next_documents(builder.issued_segments + 20):
                    if stop.is_set():
                        return
                    try:
                        status, _data, _ms = conn.request("POST", "/api/v1/documents", doc)
                        written["docs" if status == 200 else "errors"] += 1
                    except Exception:
                        written["errors"] += 1
        finally:
            conn.close()

    with_writer = _load(base_url, token, clients, seconds, probes, cpu_time, background=writer)
    return {
        "clients": clients, "read_p95_alone_ms": alone["p95_ms"], "read_p95_with_writer_ms": with_writer["p95_ms"],
        "read_rps_alone": alone["rps"], "read_rps_with_writer": with_writer["rps"],
        "read_errors_alone": alone["errors"], "read_errors_with_writer": with_writer["errors"],
        "read_client_limited_alone": alone["client_limited"],
        "read_client_limited_with_writer": with_writer["client_limited"],
        "writer_docs": written["docs"], "writer_errors": written["errors"],
    }
