from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
import pytest

from smos.bench import httpload
from smos.bench.corpus import CorpusBuilder, Probe
from smos.bench.errorlog import ErrorLog


class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    posted_docs = 0
    fail_documents = False
    fail_nodes = False

    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if type(self).fail_nodes:
            return self._send(500, {"detail": "node boom"})
        self._send(200, {"text": "x", "complete": True})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/v1/documents":
            if type(self).fail_documents:
                return self._send(500, {"detail": "ingest boom"})
            type(self).posted_docs += 1
            return self._send(200, {"status": "stored", "doc_id": "d-new"})
        query = body.get("query", "")
        if "DROP" in query:
            self.close_connection = True
            return  # no answer at all: the client sees the connection close
        if "FAIL" in query:
            return self._send(500, {"detail": "boom"})
        hit = f"found REQ-000005 here" if "REQ-000005" in query else "nothing relevant"
        self._send(200, {"results": [{"id": "a", "content": "nothing", "metadata": {"doc_id": "d1"}},
                                     {"id": "b", "content": hit, "metadata": {"doc_id": "d2"}}]})


@pytest.fixture
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    _Stub.posted_docs = 0
    _Stub.fail_documents = False
    _Stub.fail_nodes = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


GOOD = Probe("a perfectly fine query about brakes")


def test_run_step_counts_requests_and_errors(stub) -> None:
    step = httpload.run_step(stub, "tok", clients=2, seconds=0.6, probes=[GOOD, Probe("FAIL this request please")])
    assert step["clients"] == 2 and step["requests"] > 10
    assert 0 < step["errors"] < step["requests"]
    assert step["rps"] > 0 and step["p95_ms"] >= step["p50_ms"] > 0


def test_run_step_flags_client_limited_when_cpu_share_high(stub) -> None:
    ticks = iter([0.0, 5.0])
    limited = httpload.run_step(stub, "tok", clients=1, seconds=0.3, probes=[GOOD], cpu_time=lambda: next(ticks))
    assert limited["client_limited"] is True and limited["client_cpu_share"] > 0.85
    ticks = iter([0.0, 0.01])
    fine = httpload.run_step(stub, "tok", clients=1, seconds=0.3, probes=[GOOD], cpu_time=lambda: next(ticks))
    assert fine["client_limited"] is False


def test_quality_probes_hit_and_mrr_from_stub_results(stub) -> None:
    probes = [Probe("REQ-000005 brake words", "REQ-000005", 5), Probe("REQ-000009 other words", "REQ-000009", 9),
              Probe("topical query about steering")]
    q = httpload.quality_probes(stub, "tok", probes)
    assert q["n"] == 2  # topical probes have no target
    assert q["hit1"] == 0.0 and q["hit10"] == 0.5 and q["mrr"] == pytest.approx(0.25)


def test_latency_probe_summaries(stub) -> None:
    result = httpload.latency_probe(stub, "tok", [GOOD], n=10, post_docs=3)
    assert result["cold_ms"] > 0
    assert result["query"]["n"] == 10 and result["post_doc"]["n"] == 3 and result["node_text"]["n"] > 0
    assert _Stub.posted_docs == 3


def test_mixed_step_runs_a_writer_alongside_readers(stub) -> None:
    result = httpload.mixed_step(stub, "tok", clients=2, seconds=0.5, probes=[GOOD], builder=CorpusBuilder(1))
    assert result["writer_docs"] > 0 and _Stub.posted_docs == result["writer_docs"]
    assert result["read_p95_alone_ms"] > 0 and result["read_p95_with_writer_ms"] > 0


def test_bench_server_start_returns_after_health_and_stop_kills_tree(tmp_path) -> None:
    server = httpload.BenchServer(tmp_path / "data", httpload.free_port())
    seconds = server.start(timeout=180)
    try:
        assert seconds > 0 and psutil.pid_exists(server.pid)
        status, _body, _ms = httpload.request(server.base_url, "GET", "/api/v1/health")
        assert status == 200
        status, _body, _ms = httpload.request(server.base_url, "POST", "/api/v1/query",
                                              token=server.token, body={"query": "an empty store answers", "k": 3})
        assert status == 200
    finally:
        pid = server.pid
        server.stop()
    assert not psutil.pid_exists(pid)


def test_run_step_records_failures_in_the_error_log(stub) -> None:
    log = ErrorLog()
    step = httpload.run_step(stub, "tok", clients=2, seconds=0.6, probes=[GOOD, Probe("FAIL this request please")],
                             errors=log)
    summary = log.summary()
    group = summary["groups"][0]
    assert (group["phase"], group["request"], group["category"], group["example"]) == ("2 clients", "query", "http 500", "boom")
    assert summary["total"] == step["errors"] > 0


def test_dropped_connections_are_recorded_with_the_exception_class(stub) -> None:
    log = ErrorLog()
    httpload.run_step(stub, "tok", clients=1, seconds=0.4, probes=[Probe("DROP the connection now")], errors=log)
    groups = log.summary()["groups"]
    assert groups and all(g["category"] and g["category"] != "http 200" for g in groups)
    assert any("Disconnected" in g["category"] or "Error" in g["category"] or g["category"] == "timeout" for g in groups)


def test_mixed_step_records_writer_failures(stub) -> None:
    _Stub.fail_documents = True
    log = ErrorLog()
    result = httpload.mixed_step(stub, "tok", clients=1, seconds=0.4, probes=[GOOD], builder=CorpusBuilder(1), errors=log)
    assert result["writer_errors"] > 0
    writer = [g for g in log.summary()["groups"] if g["phase"] == "mixed: writer"]
    assert writer and writer[0]["request"] == "document ingest" and writer[0]["category"] == "http 500"
    assert writer[0]["count"] == result["writer_errors"] and writer[0]["example"] == "ingest boom"


def test_latency_probe_records_non_200_answers(stub) -> None:
    _Stub.fail_nodes = True
    log = ErrorLog()
    result = httpload.latency_probe(stub, "tok", [GOOD], n=5, post_docs=2, errors=log, phase="100 segments: latency")
    groups = log.summary()["groups"]
    assert result["node_text"]["n"] == 0
    assert any(g["request"] == "node text" and g["phase"] == "100 segments: latency" and g["category"] == "http 500"
               for g in groups)


def test_quality_probes_record_non_200_answers(stub) -> None:
    log = ErrorLog()
    httpload.quality_probes(stub, "tok", [Probe("FAIL needle query", "REQ-000005", 5)], errors=log,
                            phase="100 segments: quality")
    assert log.summary()["groups"][0]["phase"] == "100 segments: quality"


class _Dropper(BaseHTTPRequestHandler):
    """Closes every connection without answering."""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self.close_connection = True

    do_POST = do_GET


@pytest.fixture
def dropper():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Dropper)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_dropped_connections_in_probes_are_recorded_and_do_not_abort(dropper) -> None:
    log = ErrorLog()
    quality = httpload.quality_probes(dropper, "tok", [Probe("needle query one", "REQ-000005", 5)], errors=log,
                                      phase="q")
    latency = httpload.latency_probe(dropper, "tok", [GOOD], n=3, post_docs=1, errors=log, phase="l")
    assert quality["n"] == 0 and latency["query"]["n"] == 0
    phases = {g["phase"] for g in log.summary()["groups"]}
    assert phases == {"q", "l"}


def test_probes_without_an_error_log_still_raise_on_transport_errors(dropper) -> None:
    with pytest.raises(Exception):
        httpload.latency_probe(dropper, "tok", [GOOD], n=1, post_docs=0)
