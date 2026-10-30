from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from smos.admin import accounts, benchmarks, invitations, tokens, usage
from smos.admin.benchmark_view import build_report, history_rows
from smos.admin.dashboard_data import parse_range
from smos.admin.dashboard_view import build_dashboard
from smos.admin.tokens import SCOPES
from smos.bench.presets import PRESETS
from smos.bench.service import MAX_BUDGET_MS, BenchmarkRefused
from smos.update.apply import ApplyRefused
from smos.update.service import describe as describe_update

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def shorttime(value) -> str:
    """'2026-10-28T13:46:53.268237+00:00' -> '2026-10-28 13:46:53' (UTC), for readable tables."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


templates.env.filters["shorttime"] = shorttime

_FAIL_LIMIT = 5
_FAIL_WINDOW_S = 60.0
_NO_STORE = {"Cache-Control": "no-store"}
_PRESET_INFO = {"quick": ("Quick", "about 3-5 minutes"), "standard": ("Standard", "about 20 minutes"),
                "full": ("Full", "an hour or more")}


class Throttle:
    """In-memory failed-attempt limiter keyed by client host (per process)."""

    def __init__(self, limit: int = _FAIL_LIMIT, window: float = _FAIL_WINDOW_S) -> None:
        self._limit, self._window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q = self._fails[key]
        cutoff = time.monotonic() - self._window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self._limit

    def fail(self, key: str) -> None:
        self._trim(key).append(time.monotonic())


def client_ip(request: Request) -> str:
    if os.environ.get("SMOS_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _csrf(request: Request) -> str:
    return request.session.setdefault("csrf", secrets.token_urlsafe(16))


def _check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(submitted or "", expected):
        raise HTTPException(403, "invalid form token")


def admin_required(request: Request) -> str:
    admin = request.session.get("admin")
    if not admin:
        raise HTTPException(303, headers={"Location": "/admin/login"})
    return admin


def _public_base(request: Request) -> str:
    return (os.environ.get("SMOS_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def _db(request: Request):
    return request.app.state.admin_db


def _page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("csrf", _csrf(request) if "session" in request.scope else "")
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def _invalid_claim(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "claim_invalid.html", {}, status_code=404, headers=_NO_STORE)


def register_admin(app: FastAPI) -> APIRouter:
    app.state.throttle = Throttle()
    router = APIRouter(include_in_schema=False)

    # ------------------------------------------------------------ login
    @router.get("/admin/login", response_class=HTMLResponse)
    async def login_form(request: Request):
        return _page(request, "login.html", error=None)

    @router.post("/admin/login")
    async def login(request: Request):
        key = "login:" + client_ip(request)
        throttle: Throttle = request.app.state.throttle
        if throttle.blocked(key):
            return _page(request, "login.html", status=429, error="Too many attempts. Wait a minute.")
        form = await request.form()
        with request.app.state.admin_lock:
            ok = accounts.verify_login(_db(request), str(form.get("username", "")), str(form.get("password", "")))
        if not ok:
            throttle.fail(key)
            return _page(request, "login.html", status=401, error="Wrong username or password.")
        request.session.clear()
        request.session["admin"] = str(form.get("username"))
        request.session["csrf"] = secrets.token_urlsafe(16)
        return RedirectResponse("/admin/dashboard", status_code=303)

    @router.post("/admin/logout")
    async def logout(request: Request):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        request.session.clear()
        return RedirectResponse("/admin/login", status_code=303)

    # ------------------------------------------------------- tokens + invitations
    def _access_page(request: Request, status: int = 200, **extra):
        with request.app.state.admin_lock:
            return _page(
                request, "access.html", status=status,
                admin=request.session.get("admin"),
                tokens=tokens.list_tokens(_db(request)),
                invitations=invitations.list_invitations(_db(request)),
                **extra,
            )

    @router.get("/admin/tokens", response_class=HTMLResponse)
    async def tokens_page(request: Request, _admin: str = Depends(admin_required)):
        return _access_page(request)

    @router.get("/admin/invitations", response_class=HTMLResponse)
    async def invitations_page(request: Request, _admin: str = Depends(admin_required)):
        return _access_page(request)

    @router.post("/admin/invitations", response_class=HTMLResponse)
    async def create_invitation(request: Request, admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        label = str(form.get("label", "")).strip()
        scope = str(form.get("scope", "read")).strip() or "read"
        if scope not in SCOPES:
            return _access_page(request, status=400, error="Choose read-only or read and write.")
        if not label:
            return _access_page(request, error="Enter a label for the user.")
        ttl = int(os.environ.get("SMOS_INVITE_TTL_HOURS", "72"))
        with request.app.state.admin_lock:
            _id, secret = invitations.create_invitation(_db(request), label, admin, ttl_hours=ttl, scope=scope)
        link = f"{_public_base(request)}/claim/{secret}"
        return _page(request, "invitation_created.html", status=200, link=link, label=label, ttl=ttl,
                     scope=scope)

    @router.post("/admin/invitations/{invitation_id}/revoke")
    async def revoke_invitation(invitation_id: int, request: Request, _admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        with request.app.state.admin_lock:
            invitations.revoke_invitation(_db(request), invitation_id)
        return RedirectResponse("/admin/tokens", status_code=303)

    @router.post("/admin/tokens/{token_id}/revoke")
    async def revoke_token(token_id: int, request: Request, _admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        with request.app.state.admin_lock:
            tokens.revoke_token(_db(request), token_id)
        return RedirectResponse("/admin/tokens", status_code=303)

    @router.post("/admin/tokens/{token_id}/extend")
    async def extend_token(token_id: int, request: Request, _admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        with request.app.state.admin_lock:
            extended = tokens.extend_token(_db(request), token_id)
        if not extended:
            return _access_page(request, status=409, error=(
                f"A token can be extended only while it is active and expires within "
                f"{tokens.EXTEND_WINDOW_DAYS} days. An expired token needs Rotate."))
        return RedirectResponse("/admin/tokens", status_code=303)

    @router.post("/admin/tokens/{token_id}/rotate", response_class=HTMLResponse)
    async def rotate_token(token_id: int, request: Request, admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        ttl = int(os.environ.get("SMOS_INVITE_TTL_HOURS", "72"))
        with request.app.state.admin_lock:
            created = invitations.create_rotation_invitation(_db(request), token_id, admin, ttl_hours=ttl)
            label = next((t["name"] for t in tokens.list_tokens(_db(request)) if t["id"] == token_id), "")
        if created is None:
            raise HTTPException(404, "No such active token")
        link = f"{_public_base(request)}/claim/{created[1]}"
        return _page(request, "rotation_created.html", link=link, label=label, ttl=ttl)

    # ------------------------------------------------------------ usage
    @router.get("/admin/usage", response_class=HTMLResponse)
    async def usage_page(request: Request, token: int | None = None, _admin: str = Depends(admin_required)):
        with request.app.state.admin_lock:
            summary = usage.per_token_summary(_db(request), since_days=30)
            recent = usage.recent(_db(request), token_id=token, limit=100) if token is not None else []
        return _page(request, "usage.html", admin=request.session.get("admin"),
                     summary=summary, recent=recent, selected=token)

    @router.get("/admin/dashboard", response_class=HTMLResponse)
    def dashboard(  # a plain def: FastAPI runs it in a worker thread, off the event loop
        request: Request,
        range_: str | None = Query(None, alias="range"),
        _admin: str = Depends(admin_required),
    ):
        context = build_dashboard(request.app, parse_range(range_))
        return _page(request, "dashboard.html", admin=request.session.get("admin"), **context)

    # ------------------------------------------------------------ updates
    def _update_page(request: Request, status: int = 200, applying: bool = False, error: str | None = None):
        return _page(request, "update.html", status=status, admin=request.session.get("admin"),
                     update=describe_update(request.app), applying=applying, error=error)

    @router.get("/admin/update", response_class=HTMLResponse)
    def update_page(request: Request, applying: int = 0, _admin: str = Depends(admin_required)):
        return _update_page(request, applying=bool(applying))

    @router.get("/admin/update/status")
    def update_status(request: Request, _admin: str = Depends(admin_required)):
        info = describe_update(request.app)
        return JSONResponse({"state": info["state"], "updater_state": info["updater_state"],
                             "version": info["running"], "last_result": info["last_result"]},
                            headers=_NO_STORE)

    @router.post("/admin/update/check")
    async def update_check(request: Request, _admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        service = getattr(request.app.state, "update", None)
        if service is not None and service.checker is not None:
            await run_in_threadpool(service.checker.check, True)
        return RedirectResponse("/admin/dashboard", status_code=303)

    @router.post("/admin/update/apply")
    async def update_apply(request: Request, admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        service = getattr(request.app.state, "update", None)
        if service is None or service.updater is None:
            reason = service.disabled_reason if service is not None else None
            return _update_page(request, status=409, error=reason or "Updates are not available.")
        version, sha = str(form.get("version", "")).strip(), str(form.get("sha", "")).strip()
        if not version or not sha:
            return _update_page(request, status=409, error="No release was named; review the update and confirm again.")
        try:
            await run_in_threadpool(service.updater.begin, admin, version, sha)
        except ApplyRefused as err:
            return _update_page(request, status=409, error=str(err))
        return RedirectResponse("/admin/update?applying=1", status_code=303)

    # ------------------------------------------------------------ benchmark
    def _benchmark_page(request: Request, status: int = 200, error: str | None = None):
        service = request.app.state.bench
        with request.app.state.admin_lock:
            runs = benchmarks.list_runs(_db(request))
        presets = []
        for name, preset in PRESETS.items():
            label, eta = _PRESET_INFO[name]
            presets.append({"name": name, "label": label, "eta": eta, "sizes": preset.sizes,
                            "needs": service.describe_requirements(preset)})
        return _page(request, "benchmark.html", status=status, admin=request.session.get("admin"),
                     presets=presets, run_status=service.status(), history=history_rows(runs), error=error)

    @router.get("/admin/benchmark", response_class=HTMLResponse)
    def benchmark_page(request: Request, _admin: str = Depends(admin_required)):
        return _benchmark_page(request)

    @router.post("/admin/benchmark/start")
    async def benchmark_start(request: Request, admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        try:
            budget = float(str(form.get("budget_ms", "")).strip())
        except ValueError:
            budget = float("nan")
        if not (0 < budget <= MAX_BUDGET_MS):
            return await run_in_threadpool(
                _benchmark_page, request, 400,
                f"Enter the latency budget as a number of milliseconds between 0 and {MAX_BUDGET_MS:.0f}.")
        try:
            await run_in_threadpool(request.app.state.bench.start, admin, str(form.get("preset", "")), budget)
        except ValueError as err:
            return await run_in_threadpool(_benchmark_page, request, 400, str(err))
        except BenchmarkRefused as err:
            return await run_in_threadpool(_benchmark_page, request, 409, str(err))
        return RedirectResponse("/admin/benchmark", status_code=303)

    @router.post("/admin/benchmark/cancel")
    async def benchmark_cancel(request: Request, _admin: str = Depends(admin_required)):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf", "")))
        request.app.state.bench.cancel()
        return RedirectResponse("/admin/benchmark", status_code=303)

    @router.get("/admin/benchmark/status")
    def benchmark_status(request: Request, _admin: str = Depends(admin_required)):
        info = request.app.state.bench.status()
        return JSONResponse({"state": info["state"], "run_id": info["run_id"], "preset": info["preset"],
                             "progress": info["progress"]}, headers=_NO_STORE)

    def _stored_run(request: Request, run_id: int):
        with request.app.state.admin_lock:
            run = benchmarks.get_run(_db(request), run_id)
            previous = (benchmarks.previous_completed(_db(request), run["preset"], run["id"])
                        if run and run["status"] == "completed" else None)
        if run is None:
            raise HTTPException(404, "No such benchmark run")
        return run, previous

    @router.get("/admin/benchmark/{run_id:int}", response_class=HTMLResponse)
    def benchmark_report(run_id: int, request: Request, _admin: str = Depends(admin_required)):
        run, previous = _stored_run(request, run_id)
        return _page(request, "benchmark_report.html", admin=request.session.get("admin"),
                     report=build_report(run, previous))

    @router.get("/admin/benchmark/{run_id:int}.json")
    def benchmark_download(run_id: int, request: Request, _admin: str = Depends(admin_required)):
        run, _previous = _stored_run(request, run_id)
        return JSONResponse(run, headers={
            **_NO_STORE, "Content-Disposition": f'attachment; filename="smos-benchmark-{run_id}.json"'})

    @router.get("/admin")
    async def admin_root(_admin: str = Depends(admin_required)):
        return RedirectResponse("/admin/dashboard", status_code=303)

    # ------------------------------------------------------------ public claim
    @router.get("/claim/{secret}", response_class=HTMLResponse)
    async def claim_page(secret: str, request: Request):
        key = "claim:" + client_ip(request)
        throttle: Throttle = request.app.state.throttle
        if throttle.blocked(key):
            return HTMLResponse("Too many attempts. Try again later.", status_code=429, headers=_NO_STORE)
        with request.app.state.admin_lock:
            scope = invitations.claimable_scope(_db(request), secret)
            rotation = scope is not None and invitations.is_rotation_invitation(_db(request), secret)
        if scope is None:
            throttle.fail(key)
            return _invalid_claim(request)
        return templates.TemplateResponse(request, "claim.html", {"scope": scope, "rotation": rotation},
                                          headers=_NO_STORE)

    @router.post("/claim/{secret}", response_class=HTMLResponse)
    async def claim_submit(secret: str, request: Request):
        key = "claim:" + client_ip(request)
        throttle: Throttle = request.app.state.throttle
        if throttle.blocked(key):
            return HTMLResponse("Too many attempts. Try again later.", status_code=429, headers=_NO_STORE)
        with request.app.state.admin_lock:
            result = invitations.claim(
                _db(request), secret, client_ip(request), request.headers.get("user-agent", "")[:300]
            )
        if result is None:
            throttle.fail(key)
            return _invalid_claim(request)
        token_id, plaintext = result
        with request.app.state.admin_lock:
            scope = tokens.token_scope(_db(request), token_id)
        return templates.TemplateResponse(
            request, "token_created.html", {"token": plaintext, "scope": scope}, headers=_NO_STORE
        )

    app.include_router(router)
    return router
