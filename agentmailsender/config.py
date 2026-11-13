"""Process-level configuration, read from environment variables (prefix ``AMS_``).

Everything an operator may want to change at runtime (dashboard URL, limits,
notification channels, profiles...) lives in the database and is editable from
the web UI. Environment variables only cover what is needed to boot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool | None) -> bool | None:
    value = _env(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    if lowered == "auto":
        return None
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:  # pragma: no cover - configuration error
        raise ValueError(f"Invalid integer value for {name}: {value!r}") from exc


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(default_factory=lambda: Path("data").resolve())
    host: str = "0.0.0.0"
    port: int = 8080
    # Used to derive the key that encrypts SMTP passwords / webhook secrets at rest.
    # When unset, a random key is generated once and stored in <data_dir>/secret.key.
    secret_key: str | None = None
    # Bootstrap operator, created only when the database has no operator yet.
    admin_username: str = "admin"
    admin_password: str | None = None
    # Fallback for the dashboard URL used in notifications (the UI setting wins).
    public_url: str | None = None
    # None = automatic (Secure flag set when the request arrived over HTTPS).
    cookie_secure: bool | None = None
    session_ttl_hours: int = 12
    # Hard cap on the size of any HTTP request body (base64 attachments included).
    max_body_mb: int = 50
    # Passed to uvicorn: which proxies are trusted for X-Forwarded-* headers.
    forwarded_allow_ips: str | None = None
    log_level: str = "info"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "agentmailsender.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secret.key"

    def with_overrides(self, **overrides) -> "Config":
        return replace(self, **overrides)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            data_dir=Path(_env("AMS_DATA_DIR", "data")).expanduser().resolve(),
            host=_env("AMS_HOST", "0.0.0.0"),
            port=_env_int("AMS_PORT", 8080),
            secret_key=_env("AMS_SECRET_KEY"),
            admin_username=_env("AMS_ADMIN_USERNAME", "admin"),
            admin_password=_env("AMS_ADMIN_PASSWORD"),
            public_url=_env("AMS_PUBLIC_URL"),
            cookie_secure=_env_bool("AMS_COOKIE_SECURE", None),
            session_ttl_hours=_env_int("AMS_SESSION_TTL_HOURS", 12),
            max_body_mb=_env_int("AMS_MAX_BODY_MB", 50),
            forwarded_allow_ips=_env("AMS_FORWARDED_ALLOW_IPS"),
            log_level=_env("AMS_LOG_LEVEL", "info").lower(),
        )
