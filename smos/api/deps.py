from __future__ import annotations

from fastapi import HTTPException, Request

from smos.admin import tokens

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _authenticate(request: Request) -> int | None:
    value = _bearer(request)
    if not value:
        return None
    with request.app.state.admin_lock:
        return tokens.authenticate(request.app.state.admin_db, value)


def require_token(request: Request) -> None:
    """Every /api/v1 call except /health needs a valid, unrevoked bearer token."""
    token_id = _authenticate(request)
    if token_id is None:
        raise HTTPException(status_code=401, detail="valid bearer token required", headers=_UNAUTHORIZED)
    request.state.token_id = token_id


def require_token_or_admin_session(request: Request) -> None:
    token_id = _authenticate(request)
    if token_id is not None:
        request.state.token_id = token_id
        return
    session = request.scope.get("session") or {}
    if session.get("admin"):
        return
    raise HTTPException(status_code=401, detail="bearer token or admin session required", headers=_UNAUTHORIZED)
