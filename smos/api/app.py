from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from smos.admin import accounts, usage
from smos.admin.db import open_admin_db
from smos.admin.metrics import Sampler
from smos.admin.pages import register_admin
from smos.api import routes
from smos.api.deps import require_token, require_token_or_admin_session
from smos.launcher import UpdatePaths
from smos.memory.embeddings import model_source
from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore
from smos.update import audit as update_audit
from smos.update import service as update_service

log = logging.getLogger("smos.api")


def _version() -> str:
    """The version of the code actually loaded (an update overlay differs from the installed metadata)."""
    import smos

    return smos.__version__


_PRUNE_EVERY_S = 86400.0
_PRUNE_BATCH = 1000


class _MinuteBudget:
    """At most `per_minute` log rows per calendar minute (used for unauthenticated requests only)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._minute = -1
        self._used = 0

    def allow(self) -> bool:
        minute = int(time.time() // 60)
        if minute != self._minute:
            self._minute, self._used = minute, 0
        if self._used < self.per_minute:
            self._used += 1
            return True
        return False


def _prune_loop(app: FastAPI, days: int, sample_days: int, max_rows: int) -> None:
    """Delete expired usage rows and dashboard samples in small batches. The shared admin lock is
    held for one batch at a time, so token authentication never waits for a whole backlog."""
    while True:
        with app.state.admin_lock:
            deleted = usage.prune_batch(app.state.admin_db, days, _PRUNE_BATCH)
        if deleted < _PRUNE_BATCH:
            break
        time.sleep(0.005)
    while True:
        with app.state.admin_lock:
            deleted = usage.prune_samples_batch(app.state.admin_db, sample_days, _PRUNE_BATCH)
        if deleted < _PRUNE_BATCH:
            break
        time.sleep(0.005)
    while True:  # a hard row cap, so a flood of requests cannot outgrow the age-based retention
        with app.state.admin_lock:
            deleted = usage.prune_over_cap_batch(app.state.admin_db, max_rows, _PRUNE_BATCH)
        if deleted < _PRUNE_BATCH:
            return
        time.sleep(0.005)


def _prune_usage(app: FastAPI, background: bool = False) -> None:
    days = int(os.environ.get("SMOS_USAGE_RETENTION_DAYS", "90"))
    sample_days = int(os.environ.get("SMOS_METRICS_RETENTION_DAYS", "30"))
    max_rows = int(os.environ.get("SMOS_USAGE_MAX_ROWS", "1000000"))
    app.state.last_prune = time.monotonic()
    app.state.rows_since_prune = 0
    if not background:  # at startup nothing is being served yet
        _prune_loop(app, days, sample_days, max_rows)
        return
    thread = threading.Thread(
        target=_prune_loop, args=(app, days, sample_days, max_rows), daemon=True, name="usage-prune"
    )
    app.state.prune_thread = thread
    thread.start()


def create_app(
    data_dir: Path,
    admin_user: Optional[str] = None,
    admin_password: Optional[str] = None,
    start_sampler: bool = True,
) -> FastAPI:
    data_dir = Path(data_dir)
    model_source()  # an unusable embedding model stops startup before any file is touched
    app = FastAPI(
        title="SMOS",
        version=_version(),
        description="Semantic Memory Operating System: a store with exact-text retrieval. "
                    "SMOS never generates text; callers supply summaries.",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    admin_db = open_admin_db(data_dir)
    if admin_user and admin_password:
        accounts.bootstrap_admin(admin_db, admin_user, admin_password)
    if not accounts.has_admin(admin_db):
        admin_db.close()
        raise RuntimeError(
            "No admin account exists. Set SMOS_ADMIN_USER and SMOS_ADMIN_PASSWORD "
            "for the first start so the initial admin can be created."
        )

    try:
        update_audit.import_last_result(admin_db, UpdatePaths(data_dir))
    except Exception:
        log.exception("could not import the last update result")

    store = VectorStore(persist_path=data_dir)
    lifecycle = LifecycleManager(store)
    store._lifecycle_callback = lifecycle.on_insert

    app.state.store = store
    app.state.admin_db = admin_db
    app.state.admin_lock = threading.RLock()
    app.state.data_dir = data_dir
    app.state.maintenance = False  # set while an update restarts the server: writes get 503
    app.state.inflight = 0
    app.state.unauth_budget = _MinuteBudget(int(os.environ.get("SMOS_UNAUTH_LOG_PER_MIN", "60")))
    app.state.unauth_suppressed = 0
    app.state.prune_every_rows = int(os.environ.get("SMOS_USAGE_PRUNE_EVERY_ROWS", "10000"))
    _prune_usage(app)

    sampler = Sampler(app, interval=float(os.environ.get("SMOS_SAMPLE_INTERVAL_S", "60")))
    app.state.sampler = sampler
    if start_sampler:
        sampler.start()

    def close() -> None:
        sampler.stop()
        store.close()
        admin_db.close()

    app.state.close = close

    @app.middleware("http")
    async def log_usage(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/v1") and path != "/api/v1/health":
            token_id = getattr(request.state, "token_id", None)
            if token_id is None and not app.state.unauth_budget.allow():
                # probing without a token must not be able to grow the log without bound
                app.state.unauth_suppressed += 1
                return response
            with app.state.admin_lock:
                usage.record(
                    admin_db,
                    token_id=token_id,
                    method=request.method,
                    # the matched route template, so /nodes/<id>/text counts as one endpoint;
                    # paths that matched no route are one label, never the caller's raw text
                    path=getattr(request.scope.get("route"), "path", None) or "(unmatched)",
                    status=response.status_code,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    req_bytes=int(request.headers.get("content-length") or 0),
                    resp_bytes=int(response.headers.get("content-length") or 0),
                )
            app.state.rows_since_prune += 1
            if (app.state.rows_since_prune >= app.state.prune_every_rows
                    or time.monotonic() - app.state.last_prune > _PRUNE_EVERY_S):
                _prune_usage(app, background=True)
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=accounts.get_session_secret(admin_db),
        session_cookie="smos_admin",
        same_site="strict",
        https_only=os.environ.get("SMOS_COOKIE_SECURE") == "1",
        max_age=8 * 3600,
    )
    @app.middleware("http")
    async def maintenance_gate(request: Request, call_next):
        """Counts requests in flight (the updater waits for zero) and, during an update restart,
        refuses admin and claim changes; /api/v1 writes are refused by require_write."""
        app.state.inflight += 1
        try:
            if (app.state.maintenance and request.method not in ("GET", "HEAD", "OPTIONS")
                    and request.url.path.startswith(("/admin", "/claim"))):
                return JSONResponse({"detail": "SMOS is restarting for an update, try again shortly"},
                                    status_code=503, headers={"Retry-After": "30"})
            return await call_next(request)
        finally:
            app.state.inflight -= 1

    register_admin(app)

    open_router = APIRouter(prefix="/api/v1")

    @open_router.get("/health", summary="Liveness check",
                     description="Unauthenticated liveness probe for container healthchecks. Returns no data.")
    def health() -> dict:
        return {"status": "ok", "version": _version()}

    protected = APIRouter(dependencies=[Depends(require_token)])
    protected.include_router(routes.read)
    protected.include_router(routes.write)

    app.include_router(open_router)
    app.include_router(protected)

    @app.get("/api/v1/openapi.json", include_in_schema=False,
             dependencies=[Depends(require_token_or_admin_session)])
    def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/v1/docs", include_in_schema=False,
             dependencies=[Depends(require_token_or_admin_session)])
    def docs():
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="SMOS API")

    update_service.attach(app, data_dir)
    return app
