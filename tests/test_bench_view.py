from __future__ import annotations

import copy

from smos.admin.benchmark_view import build_report, history_rows
from tests.benchdata import make_result


def _run(result, status: str = "completed", **extra) -> dict:
    return {"id": 3, "started_at": "2026-10-30T09:00:00+00:00", "finished_at": "2026-10-30T09:20:00+00:00",
            "status": status, "preset": "standard", "budget_ms": 200.0, "smos_version": "0.3.3", "hardware": None,
            "result": result, "error": "", **extra}


def test_report_has_tiles_verdict_and_all_charts() -> None:
    report = build_report(_run(make_result()), None)
    assert report["complete"] is True
    assert [t["key"] for t in report["tiles"]] == ["peak_rps", "sustainable_rps", "query_p95_ms", "ingest_seg_per_s"]
    assert report["verdict"]["level"] == "not_needed"
    keys = [c["key"] for c in report["charts"]]
    assert keys == ["latency", "load_rps", "load_latency", "stages", "hnsw_time", "hnsw_recall", "ingest", "startup",
                    "memory_disk", "mixed", "quality"]


def test_each_chart_has_definition_and_data_table() -> None:
    for chart in build_report(_run(make_result()), None)["charts"]:
        assert chart["title"] and chart["definition"], chart["key"]
        assert chart["svg"].startswith("<svg") and "No data yet" not in chart["svg"], chart["key"]
        assert chart["table"].startswith("<table"), chart["key"]


def test_partial_result_renders_without_error() -> None:
    result = make_result()
    partial = {"schema": 1, "scaling": [result["scaling"][0]]}  # one size, no sweep, no mixed
    report = build_report(_run(partial), None)
    assert report["complete"] is False or report["charts"]  # either way: no exception, charts for what exists
    assert report["details"]["preset"] == "standard"


def test_failed_run_without_result_gives_incomplete_report_with_error() -> None:
    report = build_report(_run(None, status="failed", error="disk full"), None)
    assert report["complete"] is False and report["charts"] == [] and report["tiles"] == []
    assert "disk full" in report["message"]
    assert report["verdict"] is None


def test_cancelled_run_with_cleanup_note_only() -> None:
    report = build_report(_run({"schema": 1, "partial": True, "cleanup": "verified"}, status="cancelled"), None)
    assert report["complete"] is False and report["details"]["cleanup"] == "verified"
    assert "cancelled" in report["message"].lower()


def test_unknown_schema_version_degrades_gracefully() -> None:
    future = copy.deepcopy(make_result())
    future["schema"] = 99
    report = build_report(_run(future), None)
    assert report["complete"] is False and "newer" in report["message"].lower()
    assert report["details"]["preset"] == "standard"


def test_comparison_rows_present_when_previous_exists() -> None:
    previous = make_result(startup_s=6.0)
    report = build_report(_run(make_result(startup_s=9.0)), _run(previous))
    row = next(r for r in report["comparison"] if r["key"] == "startup_s")
    assert row["flag"] == "regression" and row["change_text"].startswith("+")
    assert build_report(_run(make_result()), None)["comparison"][0]["flag"] == "n/a"


def test_quality_chart_is_labelled_synthetic() -> None:
    chart = next(c for c in build_report(_run(make_result()), None)["charts"] if c["key"] == "quality")
    assert "synthetic" in chart["title"].lower() and "synthetic" in chart["definition"].lower()


def test_server_cpu_is_labelled_percent_of_one_core() -> None:
    result = make_result()
    for step in result["sweep"]:
        step["server_cpu_percent"] = 450.0
    chart = next(c for c in build_report(_run(result), None)["charts"] if c["key"] == "load_rps")
    assert "of one core" in chart["table"]


def test_stored_strings_are_data_not_markup() -> None:
    report = build_report(_run(make_result(), error="<script>x</script>"), None)
    assert report["details"]["preset"] == "standard"
    result = make_result()
    result["warnings"] = ["<img src=x onerror=alert(1)>"]
    chart_html = "".join(c["svg"] + c["table"] for c in build_report(_run(result), None)["charts"])
    assert "<img" not in chart_html  # warnings are passed as plain strings for the template to escape


def test_history_rows_for_cancelled_run() -> None:
    runs = [
        _run(make_result(), id=5),
        _run({"schema": 1, "partial": True}, status="cancelled", id=4),
        _run(make_result(startup_s=9.0), id=3),
        _run(make_result(startup_s=5.0), id=2),
    ]
    rows = history_rows(runs)
    assert [r["id"] for r in rows] == [5, 4, 3, 2]
    assert rows[1]["status"] == "cancelled" and rows[1]["peak_rps"] is None and rows[1]["verdict"] is None
    assert rows[0]["peak_rps"] == 41.0 and rows[0]["verdict"] == "not_needed"
    assert rows[2]["vs_previous"] == "regression"   # startup 9 s against the earlier run's 5 s
    assert rows[3]["vs_previous"] == "n/a"          # nothing older to compare with


_ERRORS = {
    "total": 7, "more_groups": 2,
    "groups": [{"phase": "mixed: writer", "request": "document ingest", "category": "http 500", "count": 5,
                "first_s": 1.25, "last_s": 9.0, "example": "<script>alert(1)</script>"},
               {"phase": "4 clients", "request": "query", "category": "timeout", "count": 2,
                "first_s": 3.0, "last_s": 3.5, "example": ""}],
    "server": {"exceptions": [{"error": "sqlite3.OperationalError: database is locked", "count": 5}],
               "locked_lines": 5, "timeout_lines": 1},
}


def test_report_has_no_errors_section_for_older_results() -> None:
    assert build_report(_run(make_result()), None)["errors"] is None


def test_report_errors_section_rows() -> None:
    errors = build_report(_run(make_result(errors=_ERRORS)), None)["errors"]
    assert errors["total"] == 7 and errors["more_groups"] == 2
    assert errors["rows"][0] == ["mixed: writer", "document ingest", "http 500", "5", "1.2 - 9.0 s", "<script>alert(1)</script>"]
    assert errors["rows"][1][:5] == ["4 clients", "query", "timeout", "2", "3.0 - 3.5 s"]
    assert errors["server_rows"] == [["sqlite3.OperationalError: database is locked", "5"]]
    assert errors["locked_lines"] == 5 and errors["timeout_lines"] == 1


def test_report_with_zero_errors_says_so() -> None:
    clean = {"total": 0, "groups": [], "more_groups": 0,
             "server": {"exceptions": [], "locked_lines": 0, "timeout_lines": 0}}
    errors = build_report(_run(make_result(errors=clean)), None)["errors"]
    assert errors["total"] == 0 and errors["rows"] == [] and errors["server_rows"] == []


def test_malformed_errors_value_degrades_instead_of_raising() -> None:
    for junk in ([], "oops", {"total": "many", "groups": "x"}, {"total": 3, "groups": [{"count": "n"}, 5, None]}):
        report = build_report(_run(make_result(errors=junk)), None)
        assert report["errors"] is None or isinstance(report["errors"], dict)
        assert report["tiles"]  # the rest of the report still builds
