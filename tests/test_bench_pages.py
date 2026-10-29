from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from smos.admin import benchmarks
from smos.bench.service import BenchmarkRefused
from tests.benchdata import make_result

PASSWORD = "correct-horse-1"


class FakeService:
    def __init__(self) -> None:
        self.started, self.cancelled, self.refuse, self.state = [], 0, None, "idle"

    def start(self, admin, preset, budget_ms):
        if self.refuse:
            raise BenchmarkRefused(self.refuse)
        self.started.append((admin, preset, budget_ms))
        return 1

    def cancel(self) -> bool:
        self.cancelled += 1
        return True

    def status(self) -> dict:
        progress = {"step": 3, "steps": 10, "label": "5000 segments: ingesting", "percent": 30}
        return {"state": self.state, "run_id": 1, "preset": "quick",
                "progress": progress if self.state != "idle" else None, "started_at": 0.0}

    def describe_requirements(self, preset) -> dict:
        return {"need_disk": 2 ** 28, "need_ram": 2 ** 31, "free_disk": 2 ** 36, "free_ram": 2 ** 33}


@pytest.fixture
def admin(api):
    client, headers, app = api
    fake = FakeService()
    app.state.bench = fake
    assert client.post("/admin/login", data={"username": "admin", "password": PASSWORD},
                       follow_redirects=False).status_code == 303
    return SimpleNamespace(client=client, headers=headers, app=app, fake=fake)


def _csrf(admin) -> str:
    page = admin.client.get("/admin/benchmark")
    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)


def _store_run(app, result, status: str = "completed", error: str = "", preset: str = "standard") -> int:
    with app.state.admin_lock:
        run_id = benchmarks.create_run(app.state.admin_db, preset=preset, budget_ms=200.0, version="0.3.3")
        benchmarks.finish_run(app.state.admin_db, run_id, status=status, result=result, hardware=None, error=error)
    return run_id


def test_anonymous_redirected_to_login(api) -> None:
    client, _h, _app = api
    for method, path in [("get", "/admin/benchmark"), ("get", "/admin/benchmark/status"),
                         ("get", "/admin/benchmark/1"), ("get", "/admin/benchmark/1.json"),
                         ("post", "/admin/benchmark/start"), ("post", "/admin/benchmark/cancel")]:
        response = getattr(client, method)(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/admin/login", path


def test_page_shows_presets_budget_and_history(admin) -> None:
    _store_run(admin.app, make_result())
    page = admin.client.get("/admin/benchmark")
    assert page.status_code == 200
    for needle in ("Quick", "Standard", "Full", 'name="budget_ms"', "Start benchmark", "maintenance", "Open report"):
        assert needle in page.text, needle
    assert "0.3.3" in page.text and "41" in page.text  # the stored run's version and peak req/s


def test_running_page_shows_progress_and_cancel(admin) -> None:
    admin.fake.state = "running"
    page = admin.client.get("/admin/benchmark")
    assert "5000 segments: ingesting" in page.text and "Cancel" in page.text and 'http-equiv="refresh"' in page.text
    assert "Start benchmark" not in page.text


def test_start_requires_csrf(admin) -> None:
    response = admin.client.post("/admin/benchmark/start", data={"preset": "quick", "budget_ms": "200"})
    assert response.status_code == 403 and admin.fake.started == []


def test_start_calls_service_and_redirects(admin) -> None:
    response = admin.client.post("/admin/benchmark/start", follow_redirects=False,
                                 data={"preset": "quick", "budget_ms": "150", "csrf": _csrf(admin)})
    assert response.status_code == 303 and response.headers["location"] == "/admin/benchmark"
    assert admin.fake.started == [("admin", "quick", 150.0)]


def test_start_refusal_shows_message_409(admin) -> None:
    admin.fake.refuse = "An update is in progress; run the benchmark afterwards."
    response = admin.client.post("/admin/benchmark/start",
                                 data={"preset": "quick", "budget_ms": "200", "csrf": _csrf(admin)})
    assert response.status_code == 409 and "An update is in progress" in response.text


@pytest.mark.parametrize("budget", ["", "0", "-5", "abc", "1e9", "nan", "inf"])
def test_start_bad_budget_400_and_nothing_started(admin, budget) -> None:
    response = admin.client.post("/admin/benchmark/start",
                                 data={"preset": "quick", "budget_ms": budget, "csrf": _csrf(admin)})
    assert response.status_code == 400 and admin.fake.started == []


@pytest.mark.parametrize("preset", ["", "nonsense", "smoke", "../../etc"])
def test_start_unknown_preset_400(admin, preset) -> None:
    from smos.bench.service import BenchmarkService

    admin.app.state.bench = BenchmarkService(admin.app, admin.app.state.data_dir)  # the real validation
    response = admin.client.post("/admin/benchmark/start",
                                 data={"preset": preset, "budget_ms": "200", "csrf": _csrf(admin)})
    assert response.status_code == 400
    assert admin.app.state.benchmark_active is False
    with admin.app.state.admin_lock:
        assert benchmarks.list_runs(admin.app.state.admin_db) == []


def test_cancel_requires_csrf_and_calls_service(admin) -> None:
    assert admin.client.post("/admin/benchmark/cancel").status_code == 403 and admin.fake.cancelled == 0
    response = admin.client.post("/admin/benchmark/cancel", data={"csrf": _csrf(admin)}, follow_redirects=False)
    assert response.status_code == 303 and admin.fake.cancelled == 1


def test_status_json_shape_and_no_store(admin) -> None:
    admin.fake.state = "running"
    response = admin.client.get("/admin/benchmark/status")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["state"] == "running" and body["progress"]["percent"] == 30 and body["run_id"] == 1


def test_report_renders_charts_verdict_and_export_button(admin) -> None:
    run_id = _store_run(admin.app, make_result())
    page = admin.client.get(f"/admin/benchmark/{run_id}")
    assert page.status_code == 200
    for needle in ("Export PDF", "window.print", "<svg", "Where a query spends its time", "HNSW is not needed",
                   "Download JSON", "Peak throughput", "SMOS 0.3.3"):
        assert needle in page.text, needle
    assert page.text.count("<svg") >= 11


def test_report_unknown_id_404_and_non_integer_id_404_not_500(admin) -> None:
    assert admin.client.get("/admin/benchmark/999").status_code == 404
    assert admin.client.get("/admin/benchmark/abc").status_code == 404
    assert admin.client.get("/admin/benchmark/999.json").status_code == 404
    assert admin.client.get("/admin/benchmark/1e5").status_code == 404


def test_report_for_failed_run_renders(admin) -> None:
    run_id = _store_run(admin.app, None, status="failed", error="<b>disk full</b>")
    page = admin.client.get(f"/admin/benchmark/{run_id}")
    assert page.status_code == 200 and "This run failed" in page.text
    assert "<b>disk full</b>" not in page.text and "&lt;b&gt;disk full" in page.text


def test_report_for_future_schema_and_garbage_results_renders(admin) -> None:
    future = make_result()
    future["schema"] = 99
    assert admin.client.get(f"/admin/benchmark/{_store_run(admin.app, future)}").status_code == 200
    for junk in ({"schema": 1, "scaling": "oops"}, {"schema": 1, "scaling": [{"segments": "x"}], "sweep": [{}]},
                 {"schema": 1, "scaling": [{}], "sweep": None, "mixed": 5}):
        assert admin.client.get(f"/admin/benchmark/{_store_run(admin.app, junk)}").status_code == 200


def test_json_download_headers(admin) -> None:
    run_id = _store_run(admin.app, make_result())
    response = admin.client.get(f"/admin/benchmark/{run_id}.json")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"] and f"smos-benchmark-{run_id}" in response.headers["content-disposition"]
    body = json.loads(response.text)
    assert body["id"] == run_id and body["result"]["schema"] == 1


def test_print_rules_hide_navigation_and_controls(admin) -> None:
    run_id = _store_run(admin.app, make_result())
    html = admin.client.get(f"/admin/benchmark/{run_id}").text
    match = re.search(r"@media print\s*\{(.*?)\n  \}", html, re.S)
    assert match, "no @media print block"
    rules = match.group(1)
    assert "header" in rules and "button" in rules and "display:none" in rules.replace(" ", "")
    assert "details" in rules and "break-inside" in rules
    assert "beforeprint" in html  # tables are expanded for Ctrl+P too


def test_nav_has_benchmark_link(admin) -> None:
    assert 'href="/admin/benchmark"' in admin.client.get("/admin/dashboard").text


# ---- a run can only be started from the admin page -----------------------------------------------------------------

def test_no_openapi_route_mentions_benchmark(api) -> None:
    _client, _h, app = api
    assert "benchmark" not in json.dumps(app.openapi()).lower()


def test_bearer_token_cannot_start_a_run(api) -> None:
    client, headers, app = api
    fake = FakeService()
    app.state.bench = fake
    response = client.post("/admin/benchmark/start", headers=headers, follow_redirects=False,
                           data={"preset": "quick", "budget_ms": "200", "csrf": "x"})
    assert response.status_code == 303 and response.headers["location"] == "/admin/login"
    assert fake.started == []


def test_get_on_start_url_is_405(admin) -> None:
    assert admin.client.get("/admin/benchmark/start").status_code == 405
    assert admin.client.get("/admin/benchmark/cancel").status_code == 405
    assert admin.fake.started == []


def test_start_has_no_other_caller() -> None:
    root = Path(__file__).resolve().parent.parent / "smos"
    callers = {}
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits = len(re.findall(r"\bbench\.start\(|\bbench\.start\b|BenchmarkService\(", text))
        if hits and path.name != "service.py":
            callers[path.relative_to(root).as_posix()] = hits
    assert callers == {"admin/pages.py": 1, "api/app.py": 1}, callers
