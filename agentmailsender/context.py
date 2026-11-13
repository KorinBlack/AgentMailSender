"""Application context: database, encryption, HTTP client and notification dispatcher."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import __version__
from .config import Config
from .db import Base, make_engine, make_session_factory
from .fields import resolve_config
from .models import MailRequest, NotificationChannel, Operator, RequestEvent, RequestStatus, Setting
from .notifiers import Event, Notifier, get_notifier_class
from .security import LoginRateLimiter, SecretBox, generate_password, hash_password
from .utils import iso, truncate, utcnow

log = logging.getLogger("agentmailsender")

RUNTIME_DEFAULTS: dict[str, Any] = {
    "dashboard_url": "",
    "max_attachments_mb": 20,
    "max_recipients": 50,
}


def get_runtime_settings(db: Session) -> dict[str, Any]:
    values = dict(RUNTIME_DEFAULTS)
    for row in db.scalars(select(Setting)):
        if row.key in RUNTIME_DEFAULTS:
            values[row.key] = row.value
    return values


def set_runtime_settings(db: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in RUNTIME_DEFAULTS:
            continue
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value


class AppContext:
    def __init__(
        self,
        config: Config,
        *,
        http_client: httpx.Client | None = None,
        sync_notifications: bool = False,
        notification_retry_delays: tuple[float, ...] = (2.0, 10.0),
    ):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(config.database_url)
        self.SessionLocal = make_session_factory(self.engine)
        self.secrets = SecretBox.from_config(config)
        self.http = http_client or httpx.Client(
            timeout=httpx.Timeout(10.0), headers={"User-Agent": f"AgentMailSender/{__version__}"}
        )
        self.login_limiter = LoginRateLimiter()
        self.notifications = NotificationDispatcher(
            self, sync=sync_notifications, retry_delays=notification_retry_delays
        )
        self.bootstrap_password: str | None = None

    # ------------------------------------------------------------------ db
    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def init_db(self, *, bootstrap: bool = True) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as db:
            if bootstrap:
                self._bootstrap_operator(db)
            self._recover_interrupted(db)
            db.commit()

    def _bootstrap_operator(self, db: Session) -> None:
        if db.scalar(select(func.count(Operator.id))):
            return
        password = self.config.admin_password or generate_password()
        db.add(Operator(username=self.config.admin_username, password_hash=hash_password(password)))
        if not self.config.admin_password:
            self.bootstrap_password = password
            banner = "=" * 72
            log.warning(
                "\n%s\nCreated the first operator account.\n  username: %s\n  password: %s\n"
                "Sign in and change it from Settings > Account.\n%s",
                banner,
                self.config.admin_username,
                password,
                banner,
            )
        else:
            log.info("Created operator %r from AMS_ADMIN_USERNAME / AMS_ADMIN_PASSWORD.", self.config.admin_username)

    def _recover_interrupted(self, db: Session) -> None:
        """A request left in 'sending' means the process died mid-delivery: never resend blindly."""
        stuck = list(db.scalars(select(MailRequest).where(MailRequest.status == RequestStatus.SENDING)))
        for req in stuck:
            req.status = RequestStatus.FAILED
            req.last_error = "Delivery was interrupted by a restart. Check the mailbox before retrying."
            req.updated_at = utcnow()
            db.add(RequestEvent(request_id=req.id, type="failed", actor="system", message=req.last_error))
        if stuck:
            log.warning("Marked %d interrupted deliveries as failed.", len(stuck))

    def dashboard_url(self, db: Session) -> str | None:
        base = (get_runtime_settings(db).get("dashboard_url") or self.config.public_url or "").strip()
        return base.rstrip("/") or None

    def close(self) -> None:
        self.notifications.shutdown()
        self.http.close()
        self.engine.dispose()


def request_event_snapshot(req: MailRequest) -> dict[str, Any]:
    return {
        "id": req.id,
        "status": req.status,
        "subject": req.subject,
        "profile": req.profile_name,
        "from": f"{req.from_name} <{req.from_email}>" if req.from_name else req.from_email,
        "to": [a["email"] for a in req.to_addrs],
        "cc": [a["email"] for a in req.cc_addrs],
        "bcc_count": len(req.bcc_addrs),
        "attachments": len(req.attachments),
        "comment": req.agent_comment,
        "decision_comment": req.decision_comment,
        "decided_by": req.decided_by,
        "error": req.last_error,
        "created_at": iso(req.created_at),
        "sent_at": iso(req.sent_at),
    }


class NotificationDispatcher:
    def __init__(self, ctx: AppContext, *, sync: bool, retry_delays: tuple[float, ...]):
        self.ctx = ctx
        self.sync = sync
        self.retry_delays = retry_delays
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")

    def build(self, channel: NotificationChannel) -> Notifier:
        cls = get_notifier_class(channel.type)
        return cls(resolve_config(cls.fields, channel.config or {}, self.ctx.secrets), self.ctx.http)

    def make_event(self, db: Session, event_type: str, req: MailRequest) -> Event:
        base = self.ctx.dashboard_url(db)
        return Event(
            type=event_type,
            occurred_at=iso(utcnow()),
            profile_id=req.profile_id,
            request=request_event_snapshot(req),
            url=f"{base}/#/inbox/{req.id}" if base else None,
        )

    def emit(self, db: Session, event_type: str, req: MailRequest) -> None:
        event = self.make_event(db, event_type, req)
        if self.sync:
            self._deliver(event)
        else:
            self._executor.submit(self._deliver_safely, event)

    def _deliver_safely(self, event: Event) -> None:
        try:
            self._deliver(event)
        except Exception:  # pragma: no cover - logged, never raised into the pool
            log.exception("Notification dispatch failed for %s", event.type)

    def _deliver(self, event: Event) -> None:
        with self.ctx.session() as db:
            channels = db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True))).all()
            for channel in channels:
                if event.type not in (channel.events or []):
                    continue
                if channel.profile_ids and event.profile_id not in channel.profile_ids:
                    continue
                error = self._send_with_retries(channel, event)
                db.execute(
                    update(NotificationChannel)
                    .where(NotificationChannel.id == channel.id)
                    .values(
                        last_status="error" if error else "ok",
                        last_error=truncate(error, 1000) if error else None,
                        last_sent_at=utcnow(),
                    )
                )
                db.commit()

    def _send_with_retries(self, channel: NotificationChannel, event: Event) -> str | None:
        error: str | None = None
        for delay in (0.0, *self.retry_delays):
            if delay:
                time.sleep(delay)
            try:
                self.build(channel).send(event)
                return None
            except Exception as exc:  # noqa: BLE001 - any failure is recorded on the channel
                error = str(exc) or exc.__class__.__name__
        log.warning("Notification channel %r (%s) failed: %s", channel.name, channel.type, error)
        return error

    def send_test(self, db: Session, channel: NotificationChannel) -> None:
        base = self.ctx.dashboard_url(db)
        event = Event(
            type="request.created",
            occurred_at=iso(utcnow()),
            profile_id=None,
            request={
                "id": "0" * 32,
                "status": "pending",
                "subject": "Test notification from AgentMailSender",
                "profile": "test-profile",
                "from": "Agent <agent@example.com>",
                "to": ["someone@example.com"],
                "cc": [],
                "bcc_count": 0,
                "attachments": 0,
                "comment": "This is a test message sent from the notification settings.",
                "decision_comment": None,
                "decided_by": None,
                "error": None,
                "created_at": iso(utcnow()),
                "sent_at": None,
            },
            url=f"{base}/#/inbox" if base else None,
        )
        self.build(channel).send(event)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
