from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils import new_id, utcnow


class RequestStatus:
    PENDING = "pending"  # waiting for an operator
    SENDING = "sending"  # approved, delivery in progress
    SENT = "sent"  # delivered to the outgoing server
    FAILED = "failed"  # approved but delivery failed (operator may retry or reject)
    REJECTED = "rejected"  # refused by an operator
    CANCELLED = "cancelled"  # withdrawn by the agent (or superseded / profile deleted)

    ALL = (PENDING, SENDING, SENT, FAILED, REJECTED, CANCELLED)
    FINAL = (SENT, REJECTED, CANCELLED)


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list["OperatorSession"]] = relationship(
        back_populates="operator", cascade="all, delete-orphan", passive_deletes=True
    )


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    operator: Mapped[Operator] = relationship(back_populates="sessions")


class Profile(Base):
    """One profile per agent: sender identities, signatures, delivery transport and API keys."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # [{"email": "...", "name": "..."}] - the first entry is the default sender.
    identities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # [{"key": "default", "label": "...", "text": "...", "html": "..."}]
    signatures: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    default_signature: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ["Name <addr>", ...] used when the agent does not provide Reply-To.
    default_reply_to: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    transport_type: Mapped[str] = mapped_column(String(32), default="smtp")
    # Field values; secret fields are stored encrypted (see security.SecretBox).
    transport_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True, order_by="ApiKey.id"
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    prefix: Mapped[str] = mapped_column(String(32), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="api_keys")


class MailRequest(Base):
    __tablename__ = "mail_requests"
    __table_args__ = (
        UniqueConstraint("profile_id", "idempotency_key", name="uq_request_idempotency"),
        Index("ix_requests_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    profile_name: Mapped[str] = mapped_column(String(64))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=RequestStatus.PENDING, index=True)

    from_email: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str] = mapped_column(String(200), default="")
    to_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    bcc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reply_to: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(998))
    # Final bodies exactly as they will be sent (signature already applied).
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extra_headers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    message_id: Mapped[str] = mapped_column(String(255))

    agent_comment: Mapped[str] = mapped_column(Text)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", passive_deletes=True, order_by="Attachment.position"
    )
    events: Mapped[list["RequestEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", passive_deletes=True, order_by="RequestEvent.id"
    )

    @property
    def recipients(self) -> list[str]:
        seen: list[str] = []
        for addr in [*self.to_addrs, *self.cc_addrs, *self.bcc_addrs]:
            email = addr["email"]
            if email.lower() not in (s.lower() for s in seen):
                seen.append(email)
        return seen


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("mail_requests.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    inline: Mapped[bool] = mapped_column(Boolean, default=False)
    content_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    request: Mapped[MailRequest] = relationship(back_populates="attachments")


class RequestEvent(Base):
    __tablename__ = "request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("mail_requests.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    type: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(100))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[MailRequest] = relationship(back_populates="events")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Empty list = every profile.
    profile_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
