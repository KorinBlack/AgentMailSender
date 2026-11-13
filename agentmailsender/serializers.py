"""JSON views of database objects."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .fields import public_config
from .mail import format_address, has_remote_content, html_to_text
from .models import ApiKey, MailRequest, NotificationChannel, Operator, Profile, RequestStatus
from .notifiers import REGISTRY as NOTIFIERS
from .transports import REGISTRY as TRANSPORTS
from .utils import iso, truncate


def _addr(a: dict[str, str]) -> dict[str, str]:
    return {"email": a["email"], "name": a.get("name") or ""}


# --------------------------------------------------------------------------- agent view


def request_for_agent(req: MailRequest) -> dict[str, Any]:
    return {
        "id": req.id,
        "status": req.status,
        "subject": req.subject,
        "from": _addr({"email": req.from_email, "name": req.from_name}),
        "to": [_addr(a) for a in req.to_addrs],
        "cc": [_addr(a) for a in req.cc_addrs],
        "bcc": [_addr(a) for a in req.bcc_addrs],
        "reply_to": [_addr(a) for a in req.reply_to],
        "signature": req.signature_key,
        "message_id": req.message_id,
        "attachments": [
            {"filename": a.filename, "content_type": a.content_type, "size": a.size, "inline": a.inline}
            for a in req.attachments
        ],
        "comment": req.agent_comment,
        "metadata": req.request_metadata or {},
        "idempotency_key": req.idempotency_key,
        "supersedes": req.supersedes_id,
        "created_at": iso(req.created_at),
        "updated_at": iso(req.updated_at),
        "decided_at": iso(req.decided_at),
        "decision_comment": req.decision_comment,
        "sent_at": iso(req.sent_at),
        "send_attempts": req.send_attempts,
        "error": req.last_error,
        "final": req.status in RequestStatus.FINAL,
    }


def profile_for_agent(profile: Profile, limits: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": profile.name,
        "description": profile.description,
        "identities": [_addr(i) for i in profile.identities or []],
        "default_reply_to": [_addr(a) for a in profile.default_reply_to or []],
        "signatures": [
            {"key": s["key"], "label": s.get("label") or "", "default": s["key"] == profile.default_signature}
            for s in profile.signatures or []
        ],
        "limits": {
            "max_recipients": limits["max_recipients"],
            "max_attachments_mb": limits["max_attachments_mb"],
        },
    }


# --------------------------------------------------------------------------- operator views


def request_summary(req: MailRequest) -> dict[str, Any]:
    body = req.body_text if req.body_text is not None else html_to_text(req.body_html or "")
    return {
        "id": req.id,
        "status": req.status,
        "subject": req.subject,
        "profile_id": req.profile_id,
        "profile_name": req.profile_name,
        "from": _addr({"email": req.from_email, "name": req.from_name}),
        "to": [_addr(a) for a in req.to_addrs],
        "cc_count": len(req.cc_addrs),
        "bcc_count": len(req.bcc_addrs),
        "attachment_count": len(req.attachments),
        "snippet": truncate(" ".join(body.split()), 160),
        "created_at": iso(req.created_at),
        "updated_at": iso(req.updated_at),
        "decided_at": iso(req.decided_at),
        "sent_at": iso(req.sent_at),
    }


def request_detail(db: Session, req: MailRequest) -> dict[str, Any]:
    data = request_summary(req)
    key = db.get(ApiKey, req.api_key_id) if req.api_key_id else None
    supersedes = db.get(MailRequest, req.supersedes_id) if req.supersedes_id else None
    superseded_by = db.scalars(select(MailRequest).where(MailRequest.supersedes_id == req.id)).all()
    data.update(
        {
            "cc": [_addr(a) for a in req.cc_addrs],
            "bcc": [_addr(a) for a in req.bcc_addrs],
            "reply_to": [_addr(a) for a in req.reply_to],
            "headers": req.extra_headers or {},
            "message_id": req.message_id,
            "signature": req.signature_key,
            "comment": req.agent_comment,
            "metadata": req.request_metadata or {},
            "idempotency_key": req.idempotency_key,
            "has_html": req.body_html is not None,
            "has_remote_content": has_remote_content(req.body_html),
            "text": req.body_text if req.body_text is not None else html_to_text(req.body_html or ""),
            "text_generated": req.body_text is None,
            "attachments": [
                {
                    "id": a.id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                    "sha256": a.sha256,
                    "inline": a.inline,
                    "content_id": a.content_id,
                }
                for a in req.attachments
            ],
            "events": [
                {"type": e.type, "actor": e.actor, "message": e.message, "created_at": iso(e.created_at)}
                for e in req.events
            ],
            "api_key": {"label": key.label, "prefix": key.prefix} if key else None,
            "supersedes": _link(supersedes),
            "superseded_by": [_link(r) for r in superseded_by],
            "decided_by": req.decided_by,
            "decision_comment": req.decision_comment,
            "send_attempts": req.send_attempts,
            "error": req.last_error,
        }
    )
    return data


def _link(req: MailRequest | None) -> dict[str, Any] | None:
    if req is None:
        return None
    return {"id": req.id, "subject": req.subject, "status": req.status, "created_at": iso(req.created_at)}


def api_key_view(key: ApiKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "label": key.label,
        "prefix": key.prefix,
        "hint": f"ams_{key.prefix}_…",
        "created_at": iso(key.created_at),
        "last_used_at": iso(key.last_used_at),
        "revoked_at": iso(key.revoked_at),
    }


def profile_view(db: Session, profile: Profile) -> dict[str, Any]:
    cls = TRANSPORTS.get(profile.transport_type)
    counts = dict(
        db.execute(
            select(MailRequest.status, func.count(MailRequest.id))
            .where(MailRequest.profile_id == profile.id)
            .group_by(MailRequest.status)
        ).all()
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "enabled": profile.enabled,
        "identities": [_addr(i) for i in profile.identities or []],
        "signatures": profile.signatures or [],
        "default_signature": profile.default_signature,
        "default_reply_to": [format_address(a) for a in profile.default_reply_to or []],
        "transport_type": profile.transport_type,
        "transport_label": cls.label if cls else profile.transport_type,
        "transport_config": public_config(cls.fields, profile.transport_config or {}) if cls else {},
        "api_keys": [api_key_view(k) for k in profile.api_keys],
        "request_counts": counts,
        "created_at": iso(profile.created_at),
        "updated_at": iso(profile.updated_at),
    }


def channel_view(channel: NotificationChannel) -> dict[str, Any]:
    cls = NOTIFIERS.get(channel.type)
    return {
        "id": channel.id,
        "name": channel.name,
        "type": channel.type,
        "type_label": cls.label if cls else channel.type,
        "enabled": channel.enabled,
        "config": public_config(cls.fields, channel.config or {}) if cls else {},
        "events": channel.events or [],
        "profile_ids": channel.profile_ids or [],
        "last_status": channel.last_status,
        "last_error": channel.last_error,
        "last_sent_at": iso(channel.last_sent_at),
        "created_at": iso(channel.created_at),
    }


def operator_view(op: Operator) -> dict[str, Any]:
    return {
        "id": op.id,
        "username": op.username,
        "created_at": iso(op.created_at),
        "last_login_at": iso(op.last_login_at),
    }
