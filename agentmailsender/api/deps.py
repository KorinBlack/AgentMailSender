from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..context import AppContext
from ..errors import ApiError
from ..models import ApiKey, Operator, OperatorSession, Profile
from ..security import hash_token, parse_api_key
from ..utils import utcnow

SESSION_COOKIE = "ams_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_db(ctx: AppContext = Depends(get_ctx)) -> Iterator[Session]:
    db = ctx.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------- agents


@dataclass
class AgentAuth:
    profile: Profile
    key: ApiKey


def _extract_api_key(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key", "").strip() or None


def require_agent(request: Request, db: Session = Depends(get_db)) -> AgentAuth:
    token = _extract_api_key(request)
    if not token:
        raise ApiError(401, "Missing API key. Send 'Authorization: Bearer <key>'.", code="unauthorized")
    prefix = parse_api_key(token)
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix)) if prefix else None
    if key is None or key.revoked_at is not None or not hmac.compare_digest(key.key_hash, hash_token(token)):
        raise ApiError(401, "Invalid or revoked API key.", code="unauthorized")
    profile = key.profile
    if not profile.enabled:
        raise ApiError(403, f"Profile {profile.name!r} is disabled.", code="profile_disabled")
    now = utcnow()
    if key.last_used_at is None or now - key.last_used_at > timedelta(seconds=60):
        key.last_used_at = now
        db.commit()
    return AgentAuth(profile=profile, key=key)


# --------------------------------------------------------------------------- operators


@dataclass
class OperatorAuth:
    operator: Operator
    session: OperatorSession


def require_operator(request: Request, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> OperatorAuth:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "Not signed in.", code="unauthorized")
    session = db.scalar(select(OperatorSession).where(OperatorSession.token_hash == hash_token(token)))
    now = utcnow()
    if session is None or session.expires_at < now:
        raise ApiError(401, "Your session has expired. Sign in again.", code="unauthorized")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, session.csrf_token):
            raise ApiError(403, "Missing or invalid CSRF token.", code="csrf")
    if now - session.last_seen_at > timedelta(minutes=1):
        session.last_seen_at = now
        session.expires_at = now + timedelta(hours=ctx.config.session_ttl_hours)
        db.commit()
    return OperatorAuth(operator=session.operator, session=session)
