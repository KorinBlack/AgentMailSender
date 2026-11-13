from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from pathlib import Path

from ..fields import Field
from .base import SendResult, Transport, register


@register
class FileTransport(Transport):
    type = "file"
    label = "Save to disk (testing)"
    description = "Writes each approved message as an .eml file in the data folder instead of sending it."
    fields = [
        Field(
            "folder",
            "Folder name",
            default="default",
            help="Sub-folder of <data>/outbox where .eml files are written.",
            placeholder="default",
        ),
    ]

    def _folder(self) -> Path:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(self.config.get("folder") or "default"))[:64] or "default"
        return self.data_dir / "outbox" / name

    def send(self, message: EmailMessage, *, envelope_from: str, recipients: list[str]) -> SendResult:
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = folder / f"{stamp}.eml"
        path.write_bytes(message.as_bytes(policy=policy.SMTP))
        path.with_suffix(".json").write_text(
            json.dumps({"envelope_from": envelope_from, "recipients": recipients}, indent=2), encoding="utf-8"
        )
        return SendResult(detail=f"Written to {path.name} in outbox/{folder.name}.")

    def test(self) -> str:
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        return f"Messages will be written to outbox/{folder.name}."
