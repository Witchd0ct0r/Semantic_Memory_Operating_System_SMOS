from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from smos.bench.service import BenchmarkRefused
from smos.admin.storeops import StoreOpRefused, open_store, paused_api, replace_store


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_flag_is_set_inside_and_cleared_after(api) -> None:
    _c, _h, app = api
    assert app.state.store_op_active is False
    with paused_api(app):
        assert app.state.store_op_active is True
    assert app.state.store_op_active is False
    with pytest.raises(RuntimeError, match="boom"):
        with paused_api(app):
            raise RuntimeError("boom")
    assert app.state.store_op_active is False


def test_second_operation_while_one_is_open_is_refused(api) -> None:
    _c, _h, app = api
    with paused_api(app):
        with pytest.raises(StoreOpRefused):
            with paused_api(app):
                pass
        assert app.state.store_op_active is True  # the refused attempt did not clear the first one's flag
    assert app.state.store_op_active is False


@pytest.mark.parametrize("flag", ["benchmark_active", "maintenance", "closing"])
def test_refused_while_another_transition_is_running(api, flag) -> None:
    _c, _h, app = api
    setattr(app.state, flag, True)
    with pytest.raises(StoreOpRefused):
        with paused_api(app):
            pass
    assert app.state.store_op_active is False


def test_drain_timeout_clears_flag_and_leaves_store_alone(api) -> None:
    _c, _h, app = api
    store = app.state.store
    app.state.api_inflight = 1  # a request that never finishes
    try:
        with pytest.raises(StoreOpRefused, match="in progress"):
            with paused_api(app, drain_timeout=0.1):
                pass
    finally:
        app.state.api_inflight = 0
    assert app.state.store_op_active is False and app.state.store is store


def test_api_answers_503_during_the_pause_but_health_and_after_work(api) -> None:
    client, headers, app = api
    with paused_api(app):
        assert client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers).status_code == 503
        assert client.get("/admin/dashboard", follow_redirects=False).status_code == 503
        assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers).status_code == 200


def test_replace_store_swaps_closes_old_and_attaches_lifecycle(api, tmp_path: Path) -> None:
    client, headers, app = api
    old = app.state.store
    with paused_api(app):
        replace_store(app, tmp_path / "other")
    new = app.state.store
    assert new is not old and new._path == tmp_path / "other"
    assert new._lifecycle_callback is not None
    with pytest.raises(Exception):
        old._db.execute("SELECT 1")  # the old connection is closed
    r = client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers)
    assert r.status_code == 200  # routes read app.state.store per request


def test_closed_store_never_rewrites_its_index_at_exit(api, tmp_path: Path) -> None:
    """After a swap the old store's atexit hook must not overwrite the files of whatever
    now lives at its path (a loaded backup would be corrupted at shutdown)."""
    _c, _h, app = api
    old = app.state.store
    index_file = old._index_file
    with paused_api(app):
        replace_store(app, tmp_path / "other")
    index_file.write_bytes(b"replaced by a later load")
    old._atexit_save()
    assert index_file.read_bytes() == b"replaced by a later load"


def test_app_close_closes_the_current_store_not_the_first_one(api, tmp_path: Path) -> None:
    _c, _h, app = api
    with paused_api(app):
        replace_store(app, tmp_path / "other")
    current = app.state.store
    app.state.close()
    with pytest.raises(Exception):
        current._db.execute("SELECT 1")


def test_benchmark_start_refused_while_store_operation_runs(api) -> None:
    _c, _h, app = api
    with paused_api(app):
        with pytest.raises(BenchmarkRefused):
            app.state.bench.start("admin", "quick", 200.0)


def test_open_store_helper_attaches_lifecycle(tmp_path: Path) -> None:
    store = open_store(tmp_path / "s")
    try:
        assert store._lifecycle_callback is not None
    finally:
        store.close()
