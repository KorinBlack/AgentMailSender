"""Declarative configuration fields.

Transports and notifiers describe their settings with :class:`Field` objects. The
same description drives validation on the server and the dynamic forms in the web
UI, so adding a new transport or notification channel needs no frontend change.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .errors import ApiError
from .security import SecretBox


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: str = "text"
    required: bool = False
    default: Any = None
    secret: bool = False
    help: str = ""
    placeholder: str = ""
    options: list[tuple[str, str]] | None = None  # (value, label) for "select"
    min: int | None = None
    max: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.options is not None:
            data["options"] = [{"value": v, "label": label} for v, label in self.options]
        return data


def describe(cls) -> dict[str, Any]:
    """Schema of a transport / notifier class, consumed by the web UI to render its form."""
    return {
        "type": cls.type,
        "label": cls.label,
        "description": cls.description,
        "fields": [f.to_dict() for f in cls.fields],
    }


def _coerce(f: Field, value: Any) -> Any:
    if f.type == "number":
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ApiError(422, f"'{f.label}' must be a whole number.", code="invalid_field", field=f.name)
        if f.min is not None and number < f.min:
            raise ApiError(422, f"'{f.label}' must be at least {f.min}.", code="invalid_field", field=f.name)
        if f.max is not None and number > f.max:
            raise ApiError(422, f"'{f.label}' must be at most {f.max}.", code="invalid_field", field=f.name)
        return number
    if f.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if f.type == "select":
        allowed = {v for v, _ in (f.options or [])}
        if value in (None, ""):
            return None
        if value not in allowed:
            raise ApiError(422, f"'{f.label}' has an invalid value.", code="invalid_field", field=f.name)
        return value
    if f.type == "json":
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ApiError(422, f"'{f.label}' is not valid JSON: {exc.msg}.", code="invalid_field", field=f.name)
        if not isinstance(value, dict):
            raise ApiError(422, f"'{f.label}' must be a JSON object.", code="invalid_field", field=f.name)
        return value
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value if f.type in {"textarea", "password"} else value.strip()
    if f.type == "url" and value and not value.lower().startswith(("http://", "https://")):
        raise ApiError(422, f"'{f.label}' must start with http:// or https://.", code="invalid_field", field=f.name)
    return value


def validate_config(
    fields: list[Field], incoming: dict[str, Any], existing: dict[str, Any] | None, box: SecretBox
) -> dict[str, Any]:
    """Validate user input and return the dict to store (secrets encrypted).

    A secret left empty keeps its stored value, so the UI never needs to read secrets back.
    Send ``null`` explicitly together with ``"__clear__": [name]`` to remove one.
    """
    existing = existing or {}
    clear = set(incoming.get("__clear__") or [])
    stored: dict[str, Any] = {}
    for f in fields:
        raw = incoming.get(f.name)
        if f.secret:
            if f.name in clear:
                value = None
            elif raw in (None, ""):
                value = existing.get(f.name)
            else:
                value = box.encrypt(_coerce(f, raw) if f.type != "json" else json.dumps(_coerce(f, raw)))
            if value in (None, "") and f.required:
                raise ApiError(422, f"'{f.label}' is required.", code="invalid_field", field=f.name)
            if value not in (None, ""):
                stored[f.name] = value
            continue
        value = _coerce(f, raw) if raw is not None else None
        if value in (None, "") and f.default is not None:
            value = f.default
        if value in (None, "") and f.required:
            raise ApiError(422, f"'{f.label}' is required.", code="invalid_field", field=f.name)
        if value not in (None, ""):
            stored[f.name] = value
    return stored


def public_config(fields: list[Field], stored: dict[str, Any]) -> dict[str, Any]:
    """Config safe to send to the browser: secrets replaced by a flag."""
    out: dict[str, Any] = {}
    secrets_set: list[str] = []
    for f in fields:
        if f.secret:
            if stored.get(f.name):
                secrets_set.append(f.name)
            continue
        if f.name in stored:
            out[f.name] = stored[f.name]
    out["__secrets_set__"] = secrets_set
    return out


def resolve_config(fields: list[Field], stored: dict[str, Any], box: SecretBox) -> dict[str, Any]:
    """Decrypted config with defaults applied, for use by a transport / notifier."""
    out: dict[str, Any] = {}
    for f in fields:
        value = stored.get(f.name)
        if f.secret and isinstance(value, str):
            value = box.decrypt(value)
            if f.type == "json" and value:
                value = json.loads(value)
        if value in (None, "") and f.default is not None:
            value = f.default
        out[f.name] = value
    return out
