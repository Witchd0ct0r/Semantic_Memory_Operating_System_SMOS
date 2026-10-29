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
