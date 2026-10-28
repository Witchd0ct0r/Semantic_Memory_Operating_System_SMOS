from __future__ import annotations

import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from smos.admin import accounts, usage
from smos.admin.db import open_admin_db
from smos.api import routes
from smos.api.deps import require_token, require_token_or_admin_session
from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore


def _version() -> str:
    try:
        return metadata.version("smos")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


def create_app(
    data_dir: Path,
    admin_user: Optional[str] = None,
    admin_password: Optional[str] = None,
) -> FastAPI:
    data_dir = Path(data_dir)
    app = FastAPI(
        title="SMOS",
        version=_version(),
        description="Semantic Memory Operating System: a store with exact-text retrieval. "
                    "SMOS never generates text; callers supply summaries.",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    store = VectorStore(persist_path=data_dir)
    lifecycle = LifecycleManager(store)
    store._lifecycle_callback = lifecycle.on_insert
    admin_db = open_admin_db(data_dir)
    if admin_user and admin_password:
        accounts.bootstrap_admin(admin_db, admin_user, admin_password)

    app.state.store = store
    app.state.admin_db = admin_db
    app.state.admin_lock = threading.RLock()
    app.state.data_dir = data_dir

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
                    path=path,
                    status=response.status_code,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    req_bytes=int(request.headers.get("content-length") or 0),
                    resp_bytes=int(response.headers.get("content-length") or 0),
                )
        return response

    open_router = APIRouter(prefix="/api/v1")

    @open_router.get("/health", summary="Liveness check",
                     description="Unauthenticated liveness probe for container healthchecks. Returns no data.")
    def health() -> dict:
        return {"status": "ok", "version": _version()}

    protected = APIRouter(dependencies=[Depends(require_token)])
    protected.include_router(routes.router)

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
