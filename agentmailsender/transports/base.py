from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

from ..fields import Field


class TransportError(Exception):
    pass


@dataclass
class SendResult:
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


class Transport:
    type: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str] = ""
    fields: ClassVar[list[Field]] = []

    def __init__(self, config: dict[str, Any], *, data_dir: Path):
        self.config = config
        self.data_dir = data_dir

    def send(self, message: EmailMessage, *, envelope_from: str, recipients: list[str]) -> SendResult:
        raise NotImplementedError

    def test(self) -> str:
        """Check the configuration (connect, authenticate...) without sending. Return a short message."""
        raise NotImplementedError


REGISTRY: dict[str, type[Transport]] = {}


def register(cls: type[Transport]) -> type[Transport]:
    REGISTRY[cls.type] = cls
    return cls


def get_transport_class(type_: str) -> type[Transport]:
    try:
        return REGISTRY[type_]
    except KeyError:
        raise TransportError(f"Unknown transport type: {type_!r}") from None
