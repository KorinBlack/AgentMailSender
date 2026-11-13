"""Request bodies (Pydantic models)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

Address = str | dict[str, str]


class AttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255, examples=["report.pdf"])
    content_base64: str = Field(description="File content, base64 encoded (a data: URL is accepted too).")
    content_type: str | None = Field(None, description="MIME type; guessed from the filename when omitted.")
    inline: bool = Field(False, description="Embed in the HTML body; reference it as cid:<content_id>.")
    content_id: str | None = Field(None, max_length=200)


class MailSubmit(BaseModel):
    """A request to send one e-mail. Nothing is sent until an operator approves it."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "to": ["Jane Doe <jane@example.com>"],
                    "cc": [{"email": "team@example.com", "name": "Team"}],
                    "subject": "Your quote for order 1042",
                    "text": "Hi Jane,\n\nplease find the quote attached.\n\nBest regards",
                    "attachments": [
                        {"filename": "quote-1042.pdf", "content_type": "application/pdf", "content_base64": "JVBERi0xLjQK..."}
                    ],
                    "comment": "Jane asked for a quote in ticket #1042 this morning; this sends the PDF generated from the CRM.",
                    "metadata": {"ticket": "1042"},
                    "idempotency_key": "ticket-1042-quote",
                }
            ]
        },
    )

    from_: str | None = Field(
        None,
        alias="from",
        description="Sender address. Must be one of the profile's identities; defaults to the first one.",
    )
    to: list[Address] = Field(default_factory=list, description='"addr", "Name <addr>" or {"email", "name"}.')
    cc: list[Address] = Field(default_factory=list)
    bcc: list[Address] = Field(default_factory=list)
    reply_to: list[Address] | None = Field(None, description="Defaults to the profile's Reply-To, if any.")
    subject: str = Field(max_length=500)
    text: str | None = Field(None, description="Plain-text body. At least one of text/html is required.")
    html: str | None = Field(None, description="HTML body. A plain-text part is generated when text is omitted.")
    signature: str | bool | None = Field(
        None,
        description="Signature key from the profile. Omit for the profile default; false or null for none.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict, description="Extra headers: In-Reply-To, References, X-* and a few others."
    )
    in_reply_to: str | None = Field(None, description="Shortcut for the In-Reply-To header (threading).")
    references: str | list[str] | None = Field(None, description="Shortcut for the References header.")
    attachments: list[AttachmentIn] = Field(default_factory=list)
    comment: str = Field(
        min_length=1,
        max_length=10000,
        description="For the human reviewer: why this e-mail should be sent and any context needed to judge it.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Free-form data echoed back to the agent.")
    idempotency_key: str | None = Field(
        None, max_length=128, description="Repeat-safe submissions: the same key returns the existing request."
    )
    supersedes: str | None = Field(
        None, description="ID of an earlier request this one replaces (e.g. a revision after a rejection)."
    )


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class DecisionIn(BaseModel):
    comment: str | None = Field(None, max_length=10000)


class IdentityIn(BaseModel):
    email: str
    name: str = ""


class SignatureIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    label: str = Field("", max_length=100)
    text: str = Field("", max_length=20000)
    html: str = Field("", max_length=100000)


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=2000)
    enabled: bool = True
    identities: list[IdentityIn] = Field(default_factory=list)
    signatures: list[SignatureIn] = Field(default_factory=list)
    default_signature: str | None = None
    default_reply_to: list[str] = Field(default_factory=list)
    transport_type: str = "smtp"
    transport_config: dict[str, Any] = Field(default_factory=dict)


class ApiKeyIn(BaseModel):
    label: str = Field("", max_length=100)


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=lambda: ["request.created"])
    profile_ids: list[int] = Field(default_factory=list)


class SettingsIn(BaseModel):
    dashboard_url: str = Field("", max_length=500)
    max_attachments_mb: int = Field(20, ge=1, le=200)
    max_recipients: int = Field(50, ge=1, le=1000)


class OperatorIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.@-]{2,64}$")
    password: str = Field(min_length=10, max_length=256)


class OperatorPasswordIn(BaseModel):
    password: str = Field(min_length=10, max_length=256)
