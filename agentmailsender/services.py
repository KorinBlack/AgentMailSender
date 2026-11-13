"""Business logic shared by the agent API and the operator API."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import mimetypes
import re
import shutil
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .context import AppContext, get_runtime_settings
from .errors import ApiError
from .fields import resolve_config, validate_config
from .mail import (
    OutgoingAttachment,
    apply_signature,
    build_message,
    clean_display_name,
    new_message_id,
    normalize_address_list,
    validate_email,
    validate_headers,
    validate_subject,
)
from .models import (
    ApiKey,
    Attachment,
    MailRequest,
    NotificationChannel,
    Operator,
    Profile,
    RequestEvent,
    RequestStatus,
)
from .notifiers import EVENT_TYPES
from .notifiers import REGISTRY as NOTIFIERS
from .schemas import ChannelIn, MailSubmit, ProfileIn
from .security import generate_api_key, hash_password
from .transports import REGISTRY as TRANSPORTS
from .transports import TransportError, get_transport_class
from .utils import new_id, utcnow

log = logging.getLogger("agentmailsender")

MAX_BODY_CHARS = 2_000_000
MAX_ATTACHMENTS = 25
_CONTENT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_CONTENT_ID_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,200}$")


def _event(req_id: str, type_: str, actor: str, message: str | None = None) -> RequestEvent:
    return RequestEvent(request_id=req_id, type=type_, actor=actor, message=message)


def attachment_path(ctx: AppContext, request_id: str, attachment_id: str) -> Path:
    return ctx.config.attachments_dir / request_id / attachment_id


# =========================================================================== submission


def _sanitize_filename(name: str) -> str:
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[:200] or "attachment"


def _decode_base64(value: str, filename: str) -> bytes:
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    value = re.sub(r"\s+", "", value)
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(
            422, f"Attachment {filename!r} is not valid base64.", code="invalid_attachment", field="attachments"
        )


def _resolve_signature(profile: Profile, data: MailSubmit) -> dict[str, Any] | None:
    signatures = {s["key"]: s for s in profile.signatures or []}
    if "signature" not in data.model_fields_set or data.signature is True:
        key = profile.default_signature
        return signatures.get(key) if key else None
    if data.signature in (None, False, ""):
        return None
    if data.signature not in signatures:
        available = ", ".join(signatures) or "none configured"
        raise ApiError(
            422,
            f"Unknown signature {data.signature!r}. Available: {available}.",
            code="unknown_signature",
            field="signature",
        )
    return signatures[data.signature]


def _resolve_identity(profile: Profile, requested: str | None) -> dict[str, str]:
    identities = profile.identities or []
    if not identities:
        raise ApiError(
            409, "This profile has no sender identity configured yet.", code="profile_not_ready"
        )
    if not requested:
        return identities[0]
    email = validate_email(requested, field="from")
    for identity in identities:
        if identity["email"].lower() == email.lower():
            return identity
    allowed = ", ".join(i["email"] for i in identities)
    raise ApiError(
        422, f"Sender {requested!r} is not allowed for this profile. Allowed: {allowed}.", code="sender_not_allowed",
        field="from",
    )


def create_mail_request(
    ctx: AppContext, db: Session, profile: Profile, api_key: ApiKey, data: MailSubmit
) -> tuple[MailRequest, bool]:
    """Validate and store a request. Returns ``(request, created)``."""
    if data.idempotency_key:
        existing = db.scalar(
            select(MailRequest).where(
                MailRequest.profile_id == profile.id, MailRequest.idempotency_key == data.idempotency_key
            )
        )
        if existing:
            return existing, False

    limits = get_runtime_settings(db)
    identity = _resolve_identity(profile, data.from_)
    to = normalize_address_list(data.to, field="to")
    cc = normalize_address_list(data.cc, field="cc")
    bcc = normalize_address_list(data.bcc, field="bcc")
    if not (to or cc or bcc):
        raise ApiError(422, "At least one recipient (to, cc or bcc) is required.", code="no_recipients", field="to")
    total = len(to) + len(cc) + len(bcc)
    if total > int(limits["max_recipients"]):
        raise ApiError(
            422, f"Too many recipients ({total}); the limit is {limits['max_recipients']}.", code="too_many_recipients"
        )
    reply_to = (
        normalize_address_list(data.reply_to, field="reply_to")
        if data.reply_to is not None
        else list(profile.default_reply_to or [])
    )
    subject = validate_subject(data.subject)
    if data.text is None and data.html is None:
        raise ApiError(422, "Provide a body: 'text', 'html' or both.", code="no_body", field="text")
    for name, body in (("text", data.text), ("html", data.html)):
        if body is not None and len(body) > MAX_BODY_CHARS:
            raise ApiError(422, f"'{name}' is too large.", code="body_too_large", field=name)

    headers = validate_headers(data.headers)
    if data.in_reply_to:
        headers.update(validate_headers({"In-Reply-To": data.in_reply_to}))
    if data.references:
        refs = data.references if isinstance(data.references, str) else " ".join(data.references)
        headers.update(validate_headers({"References": refs}))

    signature = _resolve_signature(profile, data)
    text, markup = apply_signature(data.text, data.html, signature)

    supersedes: MailRequest | None = None
    if data.supersedes:
        supersedes = db.get(MailRequest, data.supersedes)
        if supersedes is None or supersedes.profile_id != profile.id:
            raise ApiError(404, "The request in 'supersedes' does not exist.", code="not_found", field="supersedes")

    # Attachments --------------------------------------------------------
    if len(data.attachments) > MAX_ATTACHMENTS:
        raise ApiError(422, f"Too many attachments (max {MAX_ATTACHMENTS}).", code="too_many_attachments")
    decoded: list[tuple[Attachment, bytes]] = []
    total_bytes = 0
    for position, item in enumerate(data.attachments):
        filename = _sanitize_filename(item.filename)
        content = _decode_base64(item.content_base64, filename)
        total_bytes += len(content)
        content_type = (item.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream").lower()
        content_type = content_type.split(";")[0].strip()
        if not _CONTENT_TYPE_RE.match(content_type):
            raise ApiError(422, f"Invalid content type for {filename!r}.", code="invalid_attachment")
        content_id = None
        if item.inline:
            content_id = (item.content_id or "").strip().strip("<>")
            if not _CONTENT_ID_RE.match(content_id):
                raise ApiError(
                    422, f"Inline attachment {filename!r} needs a 'content_id' (letters, digits, . _ @ + -).",
                    code="invalid_attachment",
                )
        decoded.append(
            (
                Attachment(
                    id=new_id(),
                    position=position,
                    filename=filename,
                    content_type=content_type,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    inline=bool(item.inline),
                    content_id=content_id,
                ),
                content,
            )
        )
    limit_bytes = int(limits["max_attachments_mb"]) * 1024 * 1024
    if total_bytes > limit_bytes:
        raise ApiError(
            413, f"Attachments total {total_bytes / 1048576:.1f} MB; the limit is {limits['max_attachments_mb']} MB.",
            code="attachments_too_large",
        )

    now = utcnow()
    req = MailRequest(
        id=new_id(),
        profile_id=profile.id,
        profile_name=profile.name,
        api_key_id=api_key.id,
        status=RequestStatus.PENDING,
        from_email=identity["email"],
        from_name=identity.get("name") or "",
        to_addrs=to,
        cc_addrs=cc,
        bcc_addrs=bcc,
        reply_to=reply_to,
        subject=subject,
        body_text=text,
        body_html=markup,
        signature_key=signature["key"] if signature else None,
        extra_headers=headers,
        message_id=new_message_id(identity["email"]),
        agent_comment=data.comment.strip(),
        request_metadata=data.metadata,
        idempotency_key=data.idempotency_key,
        supersedes_id=supersedes.id if supersedes else None,
        created_at=now,
        updated_at=now,
    )
    folder = ctx.config.attachments_dir / req.id
    if decoded:
        folder.mkdir(parents=True, exist_ok=True)
        for att, content in decoded:
            (folder / att.id).write_bytes(content)
            req.attachments.append(att)
    key_label = api_key.label or api_key.prefix
    req.events.append(_event(req.id, "created", f"agent:{profile.name}", f"Submitted with API key “{key_label}”."))
    db.add(req)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        existing = db.scalar(
            select(MailRequest).where(
                MailRequest.profile_id == profile.id, MailRequest.idempotency_key == data.idempotency_key
            )
        )
        if existing:
            return existing, False
        raise

    superseded_pending = supersedes is not None and supersedes.status == RequestStatus.PENDING
    if superseded_pending:
        supersedes.status = RequestStatus.CANCELLED
        supersedes.updated_at = now
        db.add(_event(supersedes.id, "cancelled", f"agent:{profile.name}", f"Superseded by request {req.id}."))
    db.commit()
    ctx.notifications.emit(db, "request.created", req)
    if superseded_pending:
        ctx.notifications.emit(db, "request.cancelled", supersedes)
    return req, True


# =========================================================================== delivery


def build_mime(ctx: AppContext, req: MailRequest) -> EmailMessage:
    attachments = [
        OutgoingAttachment(
            filename=a.filename,
            content_type=a.content_type,
            data=attachment_path(ctx, req.id, a.id).read_bytes(),
            inline=a.inline,
            content_id=a.content_id,
        )
        for a in req.attachments
    ]
    return build_message(
        from_email=req.from_email,
        from_name=req.from_name,
        to=req.to_addrs,
        cc=req.cc_addrs,
        reply_to=req.reply_to,
        subject=req.subject,
        text=req.body_text,
        markup=req.body_html,
        headers=req.extra_headers or {},
        message_id=req.message_id,
        attachments=attachments,
    )


def _deliver(ctx: AppContext, db: Session, req: MailRequest) -> MailRequest:
    profile = db.get(Profile, req.profile_id) if req.profile_id else None
    try:
        if profile is None:
            raise TransportError("The profile of this request no longer exists.")
        if not profile.enabled:
            raise TransportError(f"Profile {profile.name!r} is disabled.")
        cls = get_transport_class(profile.transport_type)
        transport = cls(
            resolve_config(cls.fields, profile.transport_config or {}, ctx.secrets), data_dir=ctx.config.data_dir
        )
        message = build_mime(ctx, req)
        result = transport.send(message, envelope_from=req.from_email, recipients=req.recipients)
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the operator
        log.warning("Delivery of request %s failed: %s", req.id, exc)
        req.status = RequestStatus.FAILED
        req.last_error = str(exc) or exc.__class__.__name__
        req.send_attempts += 1
        req.updated_at = utcnow()
        db.add(_event(req.id, "failed", "system", req.last_error))
        db.commit()
        ctx.notifications.emit(db, "request.failed", req)
        return req

    now = utcnow()
    req.status = RequestStatus.SENT
    req.sent_at = now
    req.updated_at = now
    req.last_error = None
    req.send_attempts += 1
    message = " ".join([result.detail, *result.warnings]).strip() or None
    db.add(_event(req.id, "sent", "system", message))
    db.commit()
    ctx.notifications.emit(db, "request.sent", req)
    return req


def _transition(
    db: Session, req_id: str, from_states: tuple[str, ...], values: dict[str, Any], action: str
) -> MailRequest:
    """Atomic status change, so two operators can never both approve (and double-send) a request."""
    result = db.execute(
        update(MailRequest)
        .where(MailRequest.id == req_id, MailRequest.status.in_(from_states))
        .values(**values, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    req = db.get(MailRequest, req_id)
    if req is None:
        raise ApiError(404, "Request not found.", code="not_found")
    if result.rowcount == 0:
        db.refresh(req)
        raise ApiError(
            409, f"Cannot {action} a request that is {req.status}.", code="invalid_state", current_status=req.status
        )
    db.refresh(req)
    return req


def approve_request(ctx: AppContext, db: Session, req_id: str, operator: str, comment: str | None) -> MailRequest:
    now = utcnow()
    req = _transition(
        db,
        req_id,
        (RequestStatus.PENDING,),
        {
            "status": RequestStatus.SENDING,
            "decided_at": now,
            "decided_by": operator,
            "decision_comment": (comment or "").strip() or None,
        },
        "approve",
    )
    db.add(_event(req.id, "approved", f"operator:{operator}", req.decision_comment))
    db.commit()
    return _deliver(ctx, db, req)


def retry_request(ctx: AppContext, db: Session, req_id: str, operator: str) -> MailRequest:
    req = _transition(db, req_id, (RequestStatus.FAILED,), {"status": RequestStatus.SENDING}, "retry")
    db.add(_event(req.id, "retried", f"operator:{operator}"))
    db.commit()
    return _deliver(ctx, db, req)


def reject_request(ctx: AppContext, db: Session, req_id: str, operator: str, comment: str | None) -> MailRequest:
    req = _transition(
        db,
        req_id,
        (RequestStatus.PENDING, RequestStatus.FAILED),
        {
            "status": RequestStatus.REJECTED,
            "decided_at": utcnow(),
            "decided_by": operator,
            "decision_comment": (comment or "").strip() or None,
        },
        "reject",
    )
    db.add(_event(req.id, "rejected", f"operator:{operator}", req.decision_comment))
    db.commit()
    ctx.notifications.emit(db, "request.rejected", req)
    return req


def cancel_request(ctx: AppContext, db: Session, req_id: str, profile: Profile) -> MailRequest:
    req = db.get(MailRequest, req_id)
    if req is None or req.profile_id != profile.id:
        raise ApiError(404, "Request not found.", code="not_found")
    req = _transition(db, req_id, (RequestStatus.PENDING,), {"status": RequestStatus.CANCELLED}, "cancel")
    db.add(_event(req.id, "cancelled", f"agent:{profile.name}", "Withdrawn by the agent."))
    db.commit()
    ctx.notifications.emit(db, "request.cancelled", req)
    return req


# =========================================================================== profiles


def _validate_profile(ctx: AppContext, db: Session, data: ProfileIn, existing: Profile | None) -> dict[str, Any]:
    name = data.name.strip()
    if not name:
        raise ApiError(422, "Name is required.", code="invalid_field", field="name")
    clash = db.scalar(select(Profile).where(func.lower(Profile.name) == name.lower()))
    if clash is not None and (existing is None or clash.id != existing.id):
        raise ApiError(409, f"A profile named {name!r} already exists.", code="duplicate", field="name")

    identities: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data.identities:
        if not item.email.strip():
            continue
        email = validate_email(item.email, field="identities")
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        identities.append({"email": email, "name": clean_display_name(item.name, field="identities")})
    if not identities:
        raise ApiError(422, "Add at least one sender address.", code="invalid_field", field="identities")

    signatures: list[dict[str, str]] = []
    keys: set[str] = set()
    for sig in data.signatures:
        if sig.key in keys:
            raise ApiError(422, f"Duplicate signature key {sig.key!r}.", code="invalid_field", field="signatures")
        keys.add(sig.key)
        signatures.append({"key": sig.key, "label": sig.label.strip(), "text": sig.text, "html": sig.html})
    default_signature = data.default_signature or None
    if default_signature and default_signature not in keys:
        raise ApiError(422, "The default signature does not exist.", code="invalid_field", field="default_signature")

    reply_to = normalize_address_list([r for r in data.default_reply_to if r.strip()], field="default_reply_to")

    if data.transport_type not in TRANSPORTS:
        raise ApiError(422, f"Unknown transport {data.transport_type!r}.", code="invalid_field", field="transport_type")
    cls = TRANSPORTS[data.transport_type]
    previous = existing.transport_config if existing and existing.transport_type == data.transport_type else None
    transport_config = validate_config(cls.fields, data.transport_config, previous, ctx.secrets)

    return {
        "name": name,
        "description": data.description.strip(),
        "enabled": data.enabled,
        "identities": identities,
        "signatures": signatures,
        "default_signature": default_signature,
        "default_reply_to": reply_to,
        "transport_type": data.transport_type,
        "transport_config": transport_config,
    }


def create_profile(ctx: AppContext, db: Session, data: ProfileIn) -> Profile:
    profile = Profile(**_validate_profile(ctx, db, data, None))
    db.add(profile)
    db.commit()
    return profile


def update_profile(ctx: AppContext, db: Session, profile: Profile, data: ProfileIn) -> Profile:
    for key, value in _validate_profile(ctx, db, data, profile).items():
        setattr(profile, key, value)
    profile.updated_at = utcnow()
    db.commit()
    return profile


def delete_profile(ctx: AppContext, db: Session, profile: Profile) -> None:
    pending = db.scalars(
        select(MailRequest).where(MailRequest.profile_id == profile.id, MailRequest.status == RequestStatus.PENDING)
    ).all()
    for req in pending:
        req.status = RequestStatus.CANCELLED
        req.updated_at = utcnow()
        db.add(_event(req.id, "cancelled", "system", f"Profile {profile.name!r} was deleted."))
    db.delete(profile)
    db.commit()


def test_profile_transport(ctx: AppContext, profile: Profile) -> str:
    cls = get_transport_class(profile.transport_type)
    transport = cls(resolve_config(cls.fields, profile.transport_config or {}, ctx.secrets), data_dir=ctx.config.data_dir)
    return transport.test()


def create_api_key(db: Session, profile: Profile, label: str) -> tuple[ApiKey, str]:
    full, prefix, digest = generate_api_key()
    key = ApiKey(profile_id=profile.id, label=label.strip(), prefix=prefix, key_hash=digest)
    db.add(key)
    db.commit()
    return key, full


# =========================================================================== notification channels


def _validate_channel(ctx: AppContext, db: Session, data: ChannelIn, existing: NotificationChannel | None):
    if data.type not in NOTIFIERS:
        raise ApiError(422, f"Unknown channel type {data.type!r}.", code="invalid_field", field="type")
    events = [e for e in dict.fromkeys(data.events) if e]
    unknown = [e for e in events if e not in EVENT_TYPES]
    if unknown:
        raise ApiError(422, f"Unknown events: {', '.join(unknown)}.", code="invalid_field", field="events")
    if not events:
        raise ApiError(422, "Choose at least one event.", code="invalid_field", field="events")
    profile_ids = sorted(set(data.profile_ids))
    if profile_ids:
        found = set(db.scalars(select(Profile.id).where(Profile.id.in_(profile_ids))))
        if found != set(profile_ids):
            raise ApiError(422, "Unknown profile in the filter.", code="invalid_field", field="profile_ids")
    cls = NOTIFIERS[data.type]
    previous = existing.config if existing and existing.type == data.type else None
    return {
        "name": data.name.strip(),
        "type": data.type,
        "enabled": data.enabled,
        "config": validate_config(cls.fields, data.config, previous, ctx.secrets),
        "events": events,
        "profile_ids": profile_ids,
    }


def save_channel(
    ctx: AppContext, db: Session, data: ChannelIn, existing: NotificationChannel | None = None
) -> NotificationChannel:
    values = _validate_channel(ctx, db, data, existing)
    channel = existing or NotificationChannel()
    for key, value in values.items():
        setattr(channel, key, value)
    channel.updated_at = utcnow()
    if existing is None:
        db.add(channel)
    db.commit()
    return channel


# =========================================================================== operators


def create_operator(db: Session, username: str, password: str) -> Operator:
    if db.scalar(select(Operator).where(func.lower(Operator.username) == username.lower())):
        raise ApiError(409, f"Operator {username!r} already exists.", code="duplicate", field="username")
    operator = Operator(username=username, password_hash=hash_password(password))
    db.add(operator)
    db.commit()
    return operator
