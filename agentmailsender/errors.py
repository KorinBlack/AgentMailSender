from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error with an HTTP status, rendered as ``{"detail": ..., "code": ...}``."""

    def __init__(self, status: int, message: str, *, code: str = "error", **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {"detail": self.message, "code": self.code, **self.extra}
