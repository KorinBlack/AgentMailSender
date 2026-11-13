"""Notification channels. Register new ones with :func:`register`.

A notifier turns an :class:`Event` into a message for an external service and must
raise an exception when delivery fails (the dispatcher retries and records the error).
"""

from __future__ import annotations

from .base import EVENT_TYPES, REGISTRY, Event, Notifier, NotifierError, get_notifier_class, register  # noqa: F401
from . import builtin  # noqa: F401,E402  (register built-ins)

__all__ = ["EVENT_TYPES", "REGISTRY", "Event", "Notifier", "NotifierError", "get_notifier_class", "register"]
