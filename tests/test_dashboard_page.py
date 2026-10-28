from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from smos.admin import tokens
from smos.api.app import create_app

PASSWORD = "correct-horse-1"


def _login(client: TestClient) -> None:
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text


def _tile(html: str, name: str) -> str:
    match = re.search(rf'data-tile="{name}".*?class="value">([^<]*)<', html, re.S)
    assert match, f"tile {name} not found"
    return match.group(1).strip()


def _doc(native_id: str) -> dict:
    return {"external_handle": {"system": "s", "native_id": native_id},
            "blocks": [{"block_type": "paragraph", "text": f"Requirement {native_id} about radiated emissions."}]}


def test_anonymous_is_redirected_to_login(api) -> None:
    client, _h, _app = api
    r = client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"


def test_login_lands_on_the_dashboard(api) -> None:
    client, _h, _app = api
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.headers["location"] == "/admin/dashboard"
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/dashboard"


def test_page_has_every_panel_and_charts(api) -> None:
    client, _h, _app = api
    _login(client)
    page = client.get("/admin/dashboard")
    assert page.status_code == 200
    html = page.text
    for marker in ("Documents", "Segments", "Database size", "API calls", "Error rate", "Latency",
                   "CPU", "Memory", "Disk", "Most-used API calls", "Token activity", "Recent events"):
        assert marker in html, marker
    assert html.count("<svg") >= 6
    assert 'http-equiv="refresh"' in html
    assert "0.2.0" in html  # update banner slot shows the running version


def test_numbers_follow_real_traffic(api) -> None:
    client, headers, app = api
    client.post("/api/v1/documents", json=_doc("A"), headers=headers)
    client.post("/api/v1/documents", json=_doc("B"), headers=headers)
    for _ in range(3):
        client.post("/api/v1/query", json={"query": "radiated emissions requirement"}, headers=headers)
    _login(client)
    html = client.get("/admin/dashboard?range=1h").text
    assert _tile(html, "documents") == "2"
    assert _tile(html, "segments") == "2"
    assert _tile(html, "calls") == "5"
    assert "/api/v1/query" in html and "/api/v1/documents" in html


def test_every_range_works_and_an_invalid_one_falls_back(api) -> None:
    client, _h, _app = api
    _login(client)
    for value in ("1h", "24h", "7d", "30d"):
        html = client.get(f"/admin/dashboard?range={value}").text
        assert re.search(rf'class="range active"[^>]*>\s*{value}\s*<', html), value
    html = client.get("/admin/dashboard?range=bogus").text
    assert re.search(r'class="range active"[^>]*>\s*24h\s*<', html)


def test_empty_database_renders(tmp_path) -> None:
    app = create_app(tmp_path / "fresh", admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    client = TestClient(app)
    _login(client)
    page = client.get("/admin/dashboard")
    assert page.status_code == 200
    assert _tile(page.text, "calls") == "0"
    assert "No data yet" in page.text
    client.close()
    app.state.close()


def test_hostile_text_is_escaped(api) -> None:
    client, _h, app = api
    tokens.create_token(app.state.admin_db, "<script>alert(1)</script>")
    _login(client)
    html = client.get("/admin/dashboard").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_page_needs_nothing_from_the_internet(api) -> None:
    client, _h, _app = api
    _login(client)
    html = client.get("/admin/dashboard").text
    for value in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert value.startswith(("/", "#")), value
    assert set(re.findall(r"https?://[^\s\"'<>]+", html)) <= {"http://www.w3.org/2000/svg"}
    assert "<script" not in html


def test_navigation_links(api) -> None:
    client, _h, _app = api
    _login(client)
    html = client.get("/admin/dashboard").text
    for href in ("/admin/dashboard", "/admin/tokens", "/admin/usage"):
        assert f'href="{href}"' in html


def test_hardware_allocation_tiles_after_a_sample(api) -> None:
    client, _h, app = api
    assert app.state.sampler.sample_once() is True
    _login(client)
    html = client.get("/admin/dashboard").text
    for name in ("cpu_cores", "mem_limit", "disk_total"):
        assert _tile(html, name) not in ("", "n/a"), name


def test_events_show_forbidden_and_auth_failures(api) -> None:
    client, _h, app = api
    _tid, read_token = tokens.create_token(app.state.admin_db, "reader", scope="read")
    client.post("/api/v1/memories", json={"type": "doc", "content": "A write the reader must not do."},
                headers={"Authorization": f"Bearer {read_token}"})
    client.post("/api/v1/query", json={"query": "no token at all"})
    _login(client)
    html = client.get("/admin/dashboard").text
    assert "forbidden" in html and "auth failure" in html


def test_token_activity_links_to_the_usage_view(api) -> None:
    client, headers, app = api
    client.post("/api/v1/query", json={"query": "radiated emissions requirement"}, headers=headers)
    _login(client)
    token_id = tokens.list_tokens(app.state.admin_db)[0]["id"]
    assert f'href="/admin/usage?token={token_id}"' in client.get("/admin/dashboard").text


def test_sampler_lifecycle(tmp_path) -> None:
    app = create_app(tmp_path / "a", admin_user="admin", admin_password=PASSWORD)
    assert app.state.sampler.is_running()
    app.state.close()
    assert not app.state.sampler.is_running()
    off = create_app(tmp_path / "b", admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    assert not off.state.sampler.is_running()
    off.state.close()


def test_old_samples_are_pruned_at_startup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMOS_METRICS_RETENTION_DAYS", "2")
    first = create_app(tmp_path / "d", admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    first.state.admin_db.execute("INSERT INTO samples (ts) VALUES (?)", (old,))
    first.state.admin_db.commit()
    first.state.close()
    second = create_app(tmp_path / "d", admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    assert second.state.admin_db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    second.state.close()


def test_timestamps_are_short(api) -> None:
    client, headers, _app = api
    client.post("/api/v1/query", json={"query": "radiated emissions requirement"}, headers=headers)
    client.post("/api/v1/query", json={"query": "no token here at all"})
    _login(client)
    html = client.get("/admin/dashboard").text
    assert "+00:00" not in html
    assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", html)
