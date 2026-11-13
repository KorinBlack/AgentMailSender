"""Operator sign-in (cookie session + CSRF token)."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..context import AppContext
from ..errors import ApiError
from ..models import Operator, OperatorSession
from ..schemas import LoginIn, PasswordChangeIn
from ..security import dummy_verify, hash_password, hash_token, new_session_token, verify_password
from ..utils import utcnow
from .deps import SESSION_COOKIE, OperatorAuth, get_ctx, get_db, require_operator

router = APIRouter(prefix="/api/auth", include_in_schema=False)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cookie_secure(request: Request, ctx: AppContext) -> bool:
    if ctx.config.cookie_secure is not None:
        return ctx.config.cookie_secure
    return request.url.scheme == "https"


@router.post("/login")
def login(
    data: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    ip_key, user_key = f"ip:{_client_ip(request)}", f"user:{data.username.lower()}"
    if ctx.login_limiter.is_blocked(ip_key, user_key):
        raise ApiError(429, "Too many failed attempts. Try again in a few minutes.", code="rate_limited")
    operator = db.scalar(select(Operator).where(func.lower(Operator.username) == data.username.strip().lower()))
    if operator is None:
        dummy_verify(data.password)
        ok = False
    else:
        ok = verify_password(data.password, operator.password_hash)
    if not ok:
        ctx.login_limiter.record_failure(ip_key, user_key)
        raise ApiError(401, "Wrong username or password.", code="invalid_credentials")
    ctx.login_limiter.reset(user_key)

    now = utcnow()
    db.execute(delete(OperatorSession).where(OperatorSession.expires_at < now))
    token = new_session_token()
    session = OperatorSession(
        operator_id=operator.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(24),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=ctx.config.session_ttl_hours),
        ip=_client_ip(request)[:64],
        user_agent=(request.headers.get("user-agent") or "")[:255],
    )
    operator.last_login_at = now
    db.add(session)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request, ctx),
        path="/",
    )
    return {"username": operator.username, "csrf_token": session.csrf_token}


@router.post("/logout")
def logout(
    response: Response, auth: OperatorAuth = Depends(require_operator), db: Session = Depends(get_db)
) -> dict[str, Any]:
    db.delete(auth.session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(auth: OperatorAuth = Depends(require_operator), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {
        "id": auth.operator.id,
        "username": auth.operator.username,
        "csrf_token": auth.session.csrf_token,
    }


@router.post("/password")
def change_password(
    data: PasswordChangeIn, auth: OperatorAuth = Depends(require_operator), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if not verify_password(data.current_password, auth.operator.password_hash):
        raise ApiError(422, "The current password is wrong.", code="invalid_credentials", field="current_password")
    auth.operator.password_hash = hash_password(data.new_password)
    # Sign out every other session of this operator.
    db.execute(
        delete(OperatorSession).where(
            OperatorSession.operator_id == auth.operator.id, OperatorSession.id != auth.session.id
        )
    )
    db.commit()
    return {"ok": True}
