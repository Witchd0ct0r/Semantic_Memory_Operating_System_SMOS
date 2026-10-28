from __future__ import annotations

import os
import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from smos.admin import accounts, usage
from smos.admin.db import open_admin_db
from smos.admin.pages import register_admin
from smos.api import routes
from smos.api.deps import require_token, require_token_or_admin_session
from smos.memory.embeddings import model_source
from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore


def _version() -> str:
    try:
        return metadata.version("smos")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


_PRUNE_EVERY_S = 86400.0
_PRUNE_BATCH = 1000


def _prune_loop(app: FastAPI, days: int) -> None:
    """Delete expired usage rows in small batches. The shared admin lock is held for one
    batch at a time, so token authentication never waits for a whole backlog to be deleted."""
    while True:
        with app.state.admin_lock:
            deleted = usage.prune_batch(app.state.admin_db, days, _PRUNE_BATCH)
        if deleted < _PRUNE_BATCH:
            return
        time.sleep(0.005)


def _prune_usage(app: FastAPI, background: bool = False) -> None:
    days = int(os.environ.get("SMOS_USAGE_RETENTION_DAYS", "90"))
    app.state.last_prune = time.monotonic()
    if not background:  # at startup nothing is being served yet
        _prune_loop(app, days)
        return
    thread = threading.Thread(target=_prune_loop, args=(app, days), daemon=True, name="usage-prune")
    app.state.prune_thread = thread
    thread.start()


def create_app(
    data_dir: Path,
    admin_user: Optional[str] = None,
    admin_password: Optional[str] = None,
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

    store = VectorStore(persist_path=data_dir)
    lifecycle = LifecycleManager(store)
    store._lifecycle_callback = lifecycle.on_insert

    app.state.store = store
    app.state.admin_db = admin_db
    app.state.admin_lock = threading.RLock()
    app.state.data_dir = data_dir
    _prune_usage(app)

    def close() -> None:
        store.close()
        admin_db.close()

    app.state.close = close

    @app.middleware("http")
    async def log_usage(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/v1") and path != "/api/v1/health":
            with app.state.admin_lock:
                usage.record(
                    admin_db,
                    token_id=getattr(request.state, "token_id", None),
                    method=request.method,
                    # the matched route template, so /nodes/<id>/text counts as one endpoint
                    path=getattr(request.scope.get("route"), "path", None) or path,
                    status=response.status_code,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    req_bytes=int(request.headers.get("content-length") or 0),
                    resp_bytes=int(response.headers.get("content-length") or 0),
                )
            if time.monotonic() - app.state.last_prune > _PRUNE_EVERY_S:
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

    return app
