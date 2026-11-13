from __future__ import annotations

import uuid
from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC timestamp at millisecond precision.

    SQLite has no timezone support, so everything is stored as UTC. Millisecond precision matches
    :func:`iso`, so a timestamp returned by the API can be used as an exact ``updated_since`` cursor.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds") + "Z"


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` or offset accepted) into naive UTC."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def new_id() -> str:
    return uuid.uuid4().hex


def truncate(text: str | None, limit: int, ellipsis: str = "…") -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis
