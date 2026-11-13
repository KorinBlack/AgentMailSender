"""Operator API used by the web UI (``/api/admin``). Cookie session + CSRF header."""

from __future__ import annotations

import html as html_lib
import re
from email import policy
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import __version__
from ..context import AppContext, get_runtime_settings, set_runtime_settings
from ..errors import ApiError
from ..fields import describe
from ..models import ApiKey, Attachment, MailRequest, NotificationChannel, Operator, OperatorSession, Profile, RequestStatus
from ..notifiers import EVENT_TYPES
from ..notifiers import REGISTRY as NOTIFIERS
from ..schemas import (
    ApiKeyIn,
    ChannelIn,
    DecisionIn,
    OperatorIn,
    OperatorPasswordIn,
    ProfileIn,
    SettingsIn,
)
from ..security import hash_password
from ..serializers import (
    api_key_view,
    channel_view,
    operator_view,
    profile_view,
    request_detail,
    request_summary,
)
from ..services import (
    approve_request,
    attachment_path,
    build_mime,
    create_api_key,
    create_operator,
    create_profile,
    delete_profile,
    reject_request,
    retry_request,
    save_channel,
    test_profile_transport,
    update_profile,
)
from ..transports import REGISTRY as TRANSPORTS
from ..utils import utcnow
from .deps import OperatorAuth, get_ctx, get_db, require_operator

router = APIRouter(prefix="/api/admin", include_in_schema=False, dependencies=[Depends(require_operator)])


# =========================================================================== meta


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "version": __version__,
        "statuses": list(RequestStatus.ALL),
        "transports": [describe(cls) for cls in TRANSPORTS.values()],
        "notifiers": [describe(cls) for cls in NOTIFIERS.values()],
        "events": [{"type": key, "label": label} for key, label in EVENT_TYPES.items()],
    }


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = dict(db.execute(select(MailRequest.status, func.count(MailRequest.id)).group_by(MailRequest.status)).all())
    return {
        "counts": {status: counts.get(status, 0) for status in RequestStatus.ALL},
        "total": sum(counts.values()),
        "profiles": db.scalar(select(func.count(Profile.id))) or 0,
        "channels": db.scalar(select(func.count(NotificationChannel.id))) or 0,
    }


# =========================================================================== requests


def _get_request(db: Session, request_id: str) -> MailRequest:
    req = db.get(MailRequest, request_id)
    if req is None:
        raise ApiError(404, "Request not found.", code="not_found")
    return req


@router.get("/requests")
def list_requests(
    status: str = Query("pending"),
    profile_id: int | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(MailRequest)
    if status and status != "all":
        statuses = [s for s in status.split(",") if s in RequestStatus.ALL]
        query = query.where(MailRequest.status.in_(statuses))
    if profile_id:
        query = query.where(MailRequest.profile_id == profile_id)
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"
        query = query.where(
            or_(
                MailRequest.subject.ilike(like, escape="\\"),
                MailRequest.from_email.ilike(like, escape="\\"),
                MailRequest.profile_name.ilike(like, escape="\\"),
                MailRequest.agent_comment.ilike(like, escape="\\"),
                cast(MailRequest.to_addrs, String).ilike(like, escape="\\"),
                MailRequest.id == q.strip(),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.options(selectinload(MailRequest.attachments))
        .order_by(MailRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": [request_summary(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/requests/{request_id}")
def get_request(request_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return request_detail(db, _get_request(db, request_id))


_PREVIEW_STYLE = (
    "<style>html{color-scheme:light}body{margin:0;padding:20px 24px;background:#fff;color:#131416;"
    "font:15px/1.6 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;overflow-wrap:anywhere}"
    "img{max-width:100%;height:auto}pre{white-space:pre-wrap;font:inherit;margin:0}"
    "a{color:#0b5c70}</style>"
)
_INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif"}


@router.get("/requests/{request_id}/body.html")
def body_preview(
    request_id: str, remote: bool = False, format: str = Query("html"), db: Session = Depends(get_db)
) -> HTMLResponse:
    """The message body rendered for the review pane, in a locked-down sandbox."""
    req = _get_request(db, request_id)
    if format == "text" or req.body_html is None:
        text = req.body_text if req.body_text is not None else ""
        document = f"<!doctype html><html><head><meta charset='utf-8'><base target='_blank'>{_PREVIEW_STYLE}</head><body><pre>{html_lib.escape(text)}</pre></body></html>"
    else:
        markup = req.body_html
        for att in req.attachments:
            if att.content_id:
                markup = re.sub(
                    r"cid:" + re.escape(att.content_id) + r"(?=[\"'\s)>]|$)",
                    f"attachments/{att.id}/inline",
                    markup,
                    flags=re.I,
                )
        head = "<meta charset='utf-8'><base target='_blank'>"
        if re.search(r"<head[\s>]", markup, re.I):
            document = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, markup, count=1, flags=re.I)
        elif re.search(r"<html[\s>]", markup, re.I):
            document = re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + f"<head>{head}</head>", markup, count=1, flags=re.I)
        else:
            document = f"<!doctype html><html><head>{head}{_PREVIEW_STYLE}</head><body>{markup}</body></html>"
    remote_src = " https: http:" if remote else ""
    csp = (
        f"default-src 'none'; img-src 'self' data:{remote_src}; style-src 'unsafe-inline'{remote_src}; "
        f"font-src data:{remote_src}; frame-ancestors 'self'; form-action 'none'; "
        "sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox"
    )
    return HTMLResponse(
        document,
        headers={
            "Content-Security-Policy": csp,
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


def _get_attachment(db: Session, request_id: str, attachment_id: str) -> Attachment:
    att = db.get(Attachment, attachment_id)
    if att is None or att.request_id != request_id:
        raise ApiError(404, "Attachment not found.", code="not_found")
    return att


@router.get("/requests/{request_id}/attachments/{attachment_id}")
def download_attachment(
    request_id: str, attachment_id: str, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)
) -> FileResponse:
    att = _get_attachment(db, request_id, attachment_id)
    path = attachment_path(ctx, request_id, attachment_id)
    if not path.exists():
        raise ApiError(410, "The attachment file is missing from storage.", code="gone")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=att.filename,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/requests/{request_id}/attachments/{attachment_id}/inline")
def inline_attachment(
    request_id: str, attachment_id: str, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)
) -> FileResponse:
    att = _get_attachment(db, request_id, attachment_id)
    path = attachment_path(ctx, request_id, attachment_id)
    if att.content_type not in _INLINE_TYPES or not path.exists():
        raise ApiError(404, "Not an inline image.", code="not_found")
    return FileResponse(
        path,
        media_type=att.content_type,
        headers={"Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/requests/{request_id}/eml")
def download_eml(request_id: str, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> Response:
    req = _get_request(db, request_id)
    message = build_mime(ctx, req)
    return Response(
        message.as_bytes(policy=policy.SMTP),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="request-{req.id[:8]}.eml"', "Cache-Control": "no-store"},
    )


@router.post("/requests/{request_id}/approve")
def approve(
    request_id: str,
    data: DecisionIn,
    auth: OperatorAuth = Depends(require_operator),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    req = approve_request(ctx, db, request_id, auth.operator.username, data.comment)
    return request_detail(db, req)


@router.post("/requests/{request_id}/reject")
def reject(
    request_id: str,
    data: DecisionIn,
    auth: OperatorAuth = Depends(require_operator),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    req = reject_request(ctx, db, request_id, auth.operator.username, data.comment)
    return request_detail(db, req)


@router.post("/requests/{request_id}/retry")
def retry(
    request_id: str,
    auth: OperatorAuth = Depends(require_operator),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    req = retry_request(ctx, db, request_id, auth.operator.username)
    return request_detail(db, req)


# =========================================================================== profiles


def _get_profile(db: Session, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise ApiError(404, "Profile not found.", code="not_found")
    return profile


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    profiles = db.scalars(select(Profile).order_by(func.lower(Profile.name))).all()
    return [profile_view(db, p) for p in profiles]


@router.post("/profiles", status_code=201)
def add_profile(data: ProfileIn, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return profile_view(db, create_profile(ctx, db, data))


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return profile_view(db, _get_profile(db, profile_id))


@router.put("/profiles/{profile_id}")
def edit_profile(
    profile_id: int, data: ProfileIn, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    return profile_view(db, update_profile(ctx, db, _get_profile(db, profile_id), data))


@router.delete("/profiles/{profile_id}")
def remove_profile(profile_id: int, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    delete_profile(ctx, db, _get_profile(db, profile_id))
    return {"ok": True}


@router.post("/profiles/{profile_id}/test")
def test_profile(profile_id: int, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    profile = _get_profile(db, profile_id)
    try:
        return {"ok": True, "message": test_profile_transport(ctx, profile)}
    except Exception as exc:  # noqa: BLE001 - shown to the operator
        return {"ok": False, "message": str(exc) or exc.__class__.__name__}


@router.post("/profiles/{profile_id}/keys", status_code=201)
def add_key(profile_id: int, data: ApiKeyIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    key, full = create_api_key(db, _get_profile(db, profile_id), data.label)
    return {"key": full, "api_key": api_key_view(key)}


@router.delete("/profiles/{profile_id}/keys/{key_id}")
def revoke_key(profile_id: int, key_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    key = db.get(ApiKey, key_id)
    if key is None or key.profile_id != profile_id:
        raise ApiError(404, "API key not found.", code="not_found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        db.commit()
    return api_key_view(key)


# =========================================================================== notification channels


def _get_channel(db: Session, channel_id: int) -> NotificationChannel:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise ApiError(404, "Channel not found.", code="not_found")
    return channel


@router.get("/channels")
def list_channels(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [channel_view(c) for c in db.scalars(select(NotificationChannel).order_by(NotificationChannel.id)).all()]


@router.post("/channels", status_code=201)
def add_channel(data: ChannelIn, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return channel_view(save_channel(ctx, db, data))


@router.get("/channels/{channel_id}")
def get_channel(channel_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return channel_view(_get_channel(db, channel_id))


@router.put("/channels/{channel_id}")
def edit_channel(
    channel_id: int, data: ChannelIn, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    return channel_view(save_channel(ctx, db, data, _get_channel(db, channel_id)))


@router.delete("/channels/{channel_id}")
def remove_channel(channel_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    db.delete(_get_channel(db, channel_id))
    db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    try:
        ctx.notifications.send_test(db, channel)
    except Exception as exc:  # noqa: BLE001 - shown to the operator
        channel.last_status, channel.last_error, channel.last_sent_at = "error", str(exc)[:1000], utcnow()
        db.commit()
        return {"ok": False, "message": str(exc) or exc.__class__.__name__}
    channel.last_status, channel.last_error, channel.last_sent_at = "ok", None, utcnow()
    db.commit()
    return {"ok": True, "message": "Test notification delivered."}


# =========================================================================== settings


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {**get_runtime_settings(db), "public_url_env": ctx.config.public_url}


@router.put("/settings")
def put_settings(data: SettingsIn, db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    url = data.dashboard_url.strip().rstrip("/")
    if url and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url, re.I):
        raise ApiError(422, "Dashboard URL must start with http:// or https://.", code="invalid_field", field="dashboard_url")
    set_runtime_settings(
        db,
        {"dashboard_url": url, "max_attachments_mb": data.max_attachments_mb, "max_recipients": data.max_recipients},
    )
    db.commit()
    return {**get_runtime_settings(db), "public_url_env": ctx.config.public_url}


# =========================================================================== operators


@router.get("/operators")
def list_operators(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [operator_view(o) for o in db.scalars(select(Operator).order_by(Operator.id)).all()]


@router.post("/operators", status_code=201)
def add_operator(data: OperatorIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return operator_view(create_operator(db, data.username, data.password))


@router.delete("/operators/{operator_id}")
def remove_operator(
    operator_id: int, auth: OperatorAuth = Depends(require_operator), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if operator_id == auth.operator.id:
        raise ApiError(409, "You cannot delete your own account.", code="invalid_state")
    operator = db.get(Operator, operator_id)
    if operator is None:
        raise ApiError(404, "Operator not found.", code="not_found")
    db.delete(operator)
    db.commit()
    return {"ok": True}


@router.post("/operators/{operator_id}/password")
def reset_operator_password(
    operator_id: int, data: OperatorPasswordIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    operator = db.get(Operator, operator_id)
    if operator is None:
        raise ApiError(404, "Operator not found.", code="not_found")
    operator.password_hash = hash_password(data.password)
    db.query(OperatorSession).filter(OperatorSession.operator_id == operator.id).delete()
    db.commit()
    return {"ok": True}
