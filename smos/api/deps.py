from __future__ import annotations

from fastapi import HTTPException, Request

from smos.admin import tokens

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _authenticate(request: Request) -> tuple[int, str] | None:
    value = _bearer(request)
    if not value:
        return None
    with request.app.state.admin_lock:
        return tokens.authenticate_with_scope(request.app.state.admin_db, value)


def require_token(request: Request) -> None:
    """Every /api/v1 call except /health needs a valid, unrevoked bearer token."""
    found = _authenticate(request)
    if found is None:
        raise HTTPException(status_code=401, detail="valid bearer token required", headers=_UNAUTHORIZED)
    request.state.token_id, request.state.token_scope = found


def require_write(request: Request) -> None:
    """Routes that change database state need a read/write ('rw') token. A missing
    scope is treated as read-only, so a wiring mistake can only deny, never allow."""
    if getattr(request.state, "token_scope", None) != "rw":
        raise HTTPException(status_code=403, detail="this token is read-only")


def require_token_or_admin_session(request: Request) -> None:
    found = _authenticate(request)
    if found is not None:
        request.state.token_id, request.state.token_scope = found
        return
    session = request.scope.get("session") or {}
    if session.get("admin"):
        return
    raise HTTPException(status_code=401, detail="bearer token or admin session required", headers=_UNAUTHORIZED)
