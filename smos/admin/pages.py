from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from smos.admin import accounts, invitations, tokens, usage
from smos.admin.dashboard_data import parse_range
from smos.admin.dashboard_view import build_dashboard
from smos.admin.tokens import SCOPES

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
        if scope is None:
            throttle.fail(key)
            return _invalid_claim(request)
        return templates.TemplateResponse(request, "claim.html", {"scope": scope}, headers=_NO_STORE)

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
