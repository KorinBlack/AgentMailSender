"""Delivery transports. Register new ones with :func:`register`.

A transport receives a fully built :class:`email.message.EmailMessage` plus the SMTP
envelope, and must raise an exception when delivery fails.
"""

from __future__ import annotations

from .base import REGISTRY, SendResult, Transport, TransportError, get_transport_class, register  # noqa: F401
from . import file, smtp  # noqa: F401,E402  (register built-ins)

__all__ = ["REGISTRY", "SendResult", "Transport", "TransportError", "get_transport_class", "register"]
