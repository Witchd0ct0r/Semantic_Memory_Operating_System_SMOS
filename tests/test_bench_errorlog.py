from __future__ import annotations

import threading

from smos.bench import errorlog
from smos.bench.errorlog import ErrorLog, category_for, message_from_body


def test_groups_count_and_time_range_by_phase_request_and_category() -> None:
    log = ErrorLog()
    log.record("2 clients", "query", "http 500", "boom", 1.0)
    log.record("2 clients", "query", "http 500", "other text", 3.5)
    log.record("2 clients", "query", "timeout", "", 2.0)
    log.record("mixed: writer", "document ingest", "http 503", "busy", 0.5)
    summary = log.summary()
    assert summary["total"] == 4 and summary["more_groups"] == 0
    top = summary["groups"][0]
    assert (top["phase"], top["request"], top["category"], top["count"]) == ("2 clients", "query", "http 500", 2)
    assert top["first_s"] == 1.0 and top["last_s"] == 3.5 and top["example"] == "boom"
    assert {g["category"] for g in summary["groups"]} == {"http 500", "timeout", "http 503"}


def test_empty_log_summary() -> None:
    summary = ErrorLog().summary()
    assert summary["total"] == 0 and summary["groups"] == [] and summary["more_groups"] == 0
    assert summary["server"] == {"exceptions": [], "locked_lines": 0, "timeout_lines": 0}


def test_long_and_binary_messages_are_truncated() -> None:
    log = ErrorLog()
    log.record("p", "query", "http 500", "x" * 5000)
    assert len(log.summary()["groups"][0]["example"]) <= errorlog.MAX_TEXT
    assert len(message_from_body(b"\xff\xfe" * 3000)) <= errorlog.MAX_TEXT


def test_recording_from_many_threads_loses_nothing() -> None:
    log = ErrorLog()

    def work() -> None:
        for _ in range(500):
            log.record("p", "query", "http 500", "boom", 0.1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    summary = log.summary()
    assert summary["total"] == 4000 and summary["groups"][0]["count"] == 4000


def test_group_cap_reports_how_many_are_not_shown() -> None:
    log = ErrorLog()
    for i in range(errorlog.MAX_GROUPS + 7):
        log.record(f"phase {i}", "query", "http 500", "boom")
    summary = log.summary()
    assert len(summary["groups"]) == errorlog.MAX_GROUPS and summary["more_groups"] == 7
    assert summary["total"] == errorlog.MAX_GROUPS + 7


def test_category_names() -> None:
    assert category_for(None, TimeoutError()) == "timeout"
    assert category_for(503, None) == "http 503"
    assert category_for(None, ConnectionResetError("x")) == "ConnectionResetError"


def test_message_from_body() -> None:
    assert message_from_body(b'{"detail": "boom"}') == "boom"
    assert message_from_body(b'{"message": "other"}') == "other"
    assert message_from_body(b"<html>  not   json </html>") == "<html> not json </html>"
    assert message_from_body(b"") == ""
    assert message_from_body(b'{"detail": [{"msg": "bad"}]}').startswith("[")


_LOG = """INFO:     127.0.0.1:5000 - "POST /api/v1/documents HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/app/smos/memory/vector_store.py", line 10, in store_batch
    cursor.execute(sql)
sqlite3.OperationalError: database is locked
INFO:     127.0.0.1:5001 - "POST /api/v1/documents HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/app/x.py", line 1, in f
sqlite3.OperationalError: database is locked

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/app/y.py", line 2, in g
RuntimeError: secondary failure
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/app/z.py", line 3, in h
KeyError: 'missing'
WARNING:  request timed out after 30 s
"""


def test_server_log_digest_counts_root_exceptions_and_marker_lines(tmp_path) -> None:
    path = tmp_path / "server.log"
    path.write_text(_LOG, encoding="utf-8")
    log = ErrorLog()
    log.add_server_log(path)
    server = log.summary()["server"]
    assert server["exceptions"][0] == {"error": "sqlite3.OperationalError: database is locked", "count": 2}
    assert {"error": "KeyError: 'missing'", "count": 1} in server["exceptions"]
    assert all("secondary failure" not in e["error"] for e in server["exceptions"])  # chained: only the root counts
    assert server["locked_lines"] == 2 and server["timeout_lines"] == 1


def test_server_log_missing_empty_and_large_files_are_safe(tmp_path) -> None:
    log = ErrorLog()
    log.add_server_log(tmp_path / "nothing.log")
    (tmp_path / "empty.log").write_text("", encoding="utf-8")
    log.add_server_log(tmp_path / "empty.log")
    (tmp_path / "big.log").write_text('INFO:     127.0.0.1 - "POST /api/v1/query HTTP/1.1" 200 OK\n' * 20000, encoding="utf-8")
    log.add_server_log(tmp_path / "big.log")
    assert log.summary()["server"] == {"exceptions": [], "locked_lines": 0, "timeout_lines": 0}


def test_server_log_digests_merge_across_servers_and_cap_distinct_errors(tmp_path) -> None:
    log = ErrorLog()
    for i in range(30):
        path = tmp_path / f"s{i}.log"
        path.write_text(f"Traceback (most recent call last):\n  File \"a\", line 1\nValueError: distinct {i}\n", encoding="utf-8")
        log.add_server_log(path)
    assert len(log.summary()["server"]["exceptions"]) <= 20


_GROUP_LOG = """INFO:     127.0.0.1:51574 - "POST /api/v1/query HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
  + Exception Group Traceback (most recent call last):
  |   File "C:/x/uvicorn/protocols/http/httptools_impl.py", line 421, in run_asgi
  |     result = await app(
  |   File "C:/x/starlette/middleware/base.py", line 191, in __call__
  |     async with anyio.create_task_group() as task_group:
  |   File "C:/x/anyio/_backends/_asyncio.py", line 794, in __aexit__
  |     raise BaseExceptionGroup(
  | ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
  +-+---------------- 1 ----------------
    | Traceback (most recent call last):
    |   File "C:/Private/semantic_memory/smos/memory/vector_store.py", line 333, in bump_recall
    |     self._db.commit()
    |     ~~~~~~~~~~~~~~~^^
    | SystemError: error return without exception set
    +------------------------------------

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:/x/uvicorn/protocols/http/httptools_impl.py", line 421, in run_asgi
    result = await app(
  File "C:/x/starlette/middleware/base.py", line 168, in call_next
    raise app_exc from app_exc.__cause__ or app_exc.__context__
ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
"""


def test_server_log_digest_reads_exception_group_tracebacks(tmp_path) -> None:
    path = tmp_path / "server.log"
    path.write_text(_GROUP_LOG, encoding="utf-8")
    log = ErrorLog()
    log.add_server_log(path)
    assert log.summary()["server"]["exceptions"] == [
        {"error": "SystemError: error return without exception set", "count": 1}]
