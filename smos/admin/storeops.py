"""Operations that replace the running store: backup, load backup, pentest mode.

They share one mechanism. `paused_api` claims the transition slot (an update and a benchmark never
run at the same time), makes /api/v1 answer 503, and waits for requests already in flight to end.
Inside it the caller may call `replace_store`. The pause always ends, whatever happens inside.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore

_POLL_S = 0.05


class StoreOpRefused(RuntimeError):
    """The operation cannot start now; the message says why in plain words."""


def open_store(path: Path) -> VectorStore:
    store = VectorStore(persist_path=path)
    store._lifecycle_callback = LifecycleManager(store).on_insert
    return store


@contextmanager
def paused_api(app, drain_timeout: float = 30.0):
    state = app.state
    with state.transition_lock:
        if state.store_op_active:
            raise StoreOpRefused("Another store operation is already running.")
        if state.benchmark_active:
            raise StoreOpRefused("A benchmark is running; wait for it to finish or cancel it.")
        if state.maintenance or state.closing:
            raise StoreOpRefused("SMOS is restarting for an update; try again afterwards.")
        updater = getattr(getattr(state, "update", None), "updater", None)
        if updater is not None and updater.state() != "idle":
            raise StoreOpRefused("An update is in progress; try again afterwards.")
        state.store_op_active = True
    try:
        deadline = time.monotonic() + drain_timeout
        while state.api_inflight > 0:
            if time.monotonic() >= deadline:
                raise StoreOpRefused("API requests are still in progress; try again in a moment.")
            time.sleep(_POLL_S)
        yield
    finally:
        state.store_op_active = False


def replace_store(app, path: Path) -> None:
    """Close the running store and open the one at `path` in its place. Only valid inside
    `paused_api`. If opening fails, app.state.store is left pointing at the closed store: the
    caller must put a working store back before leaving the pause."""
    app.state.store.close()
    app.state.store = open_store(path)
