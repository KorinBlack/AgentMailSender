from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from ..fields import Field

EVENT_TYPES: dict[str, str] = {
    "request.created": "New mail request awaiting approval",
    "request.sent": "Mail approved and sent",
    "request.failed": "Approved mail could not be delivered",
    "request.rejected": "Mail request rejected",
    "request.cancelled": "Mail request withdrawn by the agent",
}


class NotifierError(Exception):
    pass


@dataclass
class Event:
    type: str
    occurred_at: str
    profile_id: int | None
    request: dict[str, Any]
    url: str | None

    @property
    def headline(self) -> str:
        return EVENT_TYPES.get(self.type, self.type)

    def payload(self) -> dict[str, Any]:
        return {
            "event": self.type,
            "headline": self.headline,
            "occurred_at": self.occurred_at,
            "dashboard_url": self.url,
            "request": self.request,
        }

    def placeholders(self) -> dict[str, str]:
        r = self.request
        return {
            "event": self.type,
            "headline": self.headline,
            "occurred_at": self.occurred_at,
            "url": self.url or "",
            "id": r.get("id", ""),
            "status": r.get("status", ""),
            "subject": r.get("subject", ""),
            "profile": r.get("profile", ""),
            "from": r.get("from", ""),
            "to": ", ".join(r.get("to") or []),
            "comment": r.get("comment") or "",
            "decision_comment": r.get("decision_comment") or "",
            "error": r.get("error") or "",
        }


class Notifier:
    type: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str] = ""
    fields: ClassVar[list[Field]] = []

    def __init__(self, config: dict[str, Any], http: httpx.Client):
        self.config = config
        self.http = http

    def send(self, event: Event) -> None:
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            body = response.text.strip().replace("\n", " ")[:300]
            raise NotifierError(f"HTTP {response.status_code} from {response.request.url.host}: {body}")
        return response

    def _post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = self.http.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise NotifierError(f"Request failed: {exc}") from exc
        return self._check(response)


REGISTRY: dict[str, type[Notifier]] = {}


def register(cls: type[Notifier]) -> type[Notifier]:
    REGISTRY[cls.type] = cls
    return cls


def get_notifier_class(type_: str) -> type[Notifier]:
    try:
        return REGISTRY[type_]
    except KeyError:
        raise NotifierError(f"Unknown notification channel type: {type_!r}") from None
