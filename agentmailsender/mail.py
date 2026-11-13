"""Address validation, signatures and MIME message construction."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
from html.parser import HTMLParser
from typing import Any

from .errors import ApiError

# --------------------------------------------------------------------------- addresses

_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{0,62}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_email(value: str, *, field: str = "address") -> str:
    """Return a normalised address (domain lower-cased and IDNA-encoded) or raise ApiError."""
    addr = (value or "").strip()
    if len(addr) > 254 or addr.count("@") != 1:
        raise ApiError(422, f"Invalid e-mail address in '{field}': {value!r}.", code="invalid_address", field=field)
    local, domain = addr.split("@")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ApiError(422, f"Invalid domain in '{field}': {value!r}.", code="invalid_address", field=field)
    if not local or len(local) > 64 or not _LOCAL_RE.match(local) or not _DOMAIN_RE.match(domain):
        raise ApiError(422, f"Invalid e-mail address in '{field}': {value!r}.", code="invalid_address", field=field)
    return f"{local}@{domain}"


def clean_display_name(name: str | None, *, field: str = "name") -> str:
    name = (name or "").strip()
    if _CTRL_RE.search(name):
        raise ApiError(422, f"Control characters are not allowed in '{field}'.", code="invalid_header", field=field)
    if len(name) > 200:
        raise ApiError(422, f"'{field}' is too long (max 200 characters).", code="invalid_header", field=field)
    return name


def normalize_address(value: Any, *, field: str) -> dict[str, str]:
    """Accept ``"addr"``, ``"Name <addr>"`` or ``{"email": ..., "name": ...}``."""
    if isinstance(value, dict):
        email = value.get("email") or value.get("address") or ""
        name = value.get("name") or ""
    elif isinstance(value, str):
        if _CTRL_RE.search(value):
            raise ApiError(422, f"Control characters are not allowed in '{field}'.", code="invalid_header", field=field)
        name, email = parseaddr(value)
        if not email:
            raise ApiError(422, f"Invalid e-mail address in '{field}': {value!r}.", code="invalid_address", field=field)
    else:
        raise ApiError(422, f"Invalid address in '{field}'.", code="invalid_address", field=field)
    return {"email": validate_email(str(email), field=field), "name": clean_display_name(str(name), field=field)}


def normalize_address_list(values: Any, *, field: str) -> list[dict[str, str]]:
    if values is None:
        return []
    if isinstance(values, (str, dict)):
        values = [values]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        addr = normalize_address(value, field=field)
        key = addr["email"].lower()
        if key not in seen:
            seen.add(key)
            result.append(addr)
    return result


def format_address(addr: dict[str, str]) -> str:
    name = addr.get("name") or ""
    return str(Address(display_name=name, addr_spec=addr["email"])) if name else addr["email"]


# --------------------------------------------------------------------------- headers

# Headers an agent may set. Everything that defines the envelope or the sender is controlled
# by the profile; X-* headers are allowed for tracing/integration purposes.
ALLOWED_HEADERS = {
    "in-reply-to": "In-Reply-To",
    "references": "References",
    "importance": "Importance",
    "priority": "Priority",
    "x-priority": "X-Priority",
    "auto-submitted": "Auto-Submitted",
    "list-unsubscribe": "List-Unsubscribe",
    "list-unsubscribe-post": "List-Unsubscribe-Post",
}
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,76}$")


def validate_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.match(name):
            raise ApiError(422, f"Invalid header name: {name!r}.", code="invalid_header", field="headers")
        lowered = name.lower()
        if lowered in ALLOWED_HEADERS:
            canonical = ALLOWED_HEADERS[lowered]
        elif lowered.startswith("x-"):
            canonical = name
        else:
            raise ApiError(
                422,
                f"Header {name!r} cannot be set by agents. Allowed: {', '.join(ALLOWED_HEADERS.values())} and X-*.",
                code="header_not_allowed",
                field="headers",
            )
        text = "" if value is None else str(value)
        if _CTRL_RE.search(text.replace("\t", " ")) or len(text) > 2000:
            raise ApiError(422, f"Invalid value for header {name!r}.", code="invalid_header", field="headers")
        if text:
            result[canonical] = text
    return result


def validate_subject(subject: str) -> str:
    subject = (subject or "").strip()
    if not subject:
        raise ApiError(422, "'subject' must not be empty.", code="invalid_subject", field="subject")
    if _CTRL_RE.search(subject.replace("\t", " ")):
        raise ApiError(422, "Line breaks are not allowed in 'subject'.", code="invalid_subject", field="subject")
    if len(subject) > 500:
        raise ApiError(422, "'subject' is too long (max 500 characters).", code="invalid_subject", field="subject")
    return subject


# --------------------------------------------------------------------------- HTML helpers


class _TextExtractor(HTMLParser):
    BLOCKS = {
        "p", "div", "tr", "table", "ul", "ol", "blockquote", "section", "article", "header", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "address", "center",
    }  # fmt: skip
    SKIP = {"script", "style", "title", "template", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._links: list[tuple[str | None, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCKS:
            self.parts.append("\n\n" if tag in {"p", "h1", "h2", "h3", "blockquote", "table"} else "\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")
        if tag == "a":
            self._links.append((dict(attrs).get("href"), len(self.parts)))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n\n" if tag in {"p", "h1", "h2", "h3", "blockquote", "table"} else "\n")
        elif tag == "a" and self._links:
            href, start = self._links.pop()
            label = "".join(self.parts[start:]).strip()
            if href and href.lower().startswith(("http://", "https://")) and href not in label:
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in raw.split("\n")]
        joined = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.text()


def text_to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>\n")


_REMOTE_IMG_RE = re.compile(r"""(?:<img[^>]+src|background)\s*=\s*["']?\s*https?://|url\(\s*["']?\s*https?://""", re.I)


def has_remote_content(markup: str | None) -> bool:
    return bool(markup and _REMOTE_IMG_RE.search(markup))


# --------------------------------------------------------------------------- signatures

SIGNATURE_DELIMITER = "\n\n-- \n"


def apply_signature(text: str | None, markup: str | None, signature: dict[str, Any] | None):
    if not signature:
        return text, markup
    sig_text = (signature.get("text") or "").strip()
    sig_html = (signature.get("html") or "").strip()
    if not sig_text and sig_html:
        sig_text = html_to_text(sig_html)
    if not sig_html and sig_text:
        sig_html = text_to_html(sig_text)
    if text is not None and sig_text:
        text = text.rstrip() + SIGNATURE_DELIMITER + sig_text
    if markup is not None and sig_html:
        block = f'<div class="signature" style="margin-top:16px">{sig_html}</div>'
        match = re.search(r"</body\s*>", markup, re.I)
        markup = markup[: match.start()] + block + markup[match.start() :] if match else markup + block
    return text, markup


# --------------------------------------------------------------------------- message building


@dataclass
class OutgoingAttachment:
    filename: str
    content_type: str
    data: bytes
    inline: bool = False
    content_id: str | None = None


def new_message_id(from_email: str) -> str:
    domain = from_email.rsplit("@", 1)[-1]
    return make_msgid(domain=domain)


def build_message(
    *,
    from_email: str,
    from_name: str,
    to: list[dict[str, str]],
    cc: list[dict[str, str]],
    reply_to: list[dict[str, str]],
    subject: str,
    text: str | None,
    markup: str | None,
    headers: dict[str, str],
    message_id: str,
    attachments: list[OutgoingAttachment],
    date: datetime | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = Address(display_name=from_name or "", addr_spec=from_email)
    if to:
        msg["To"] = [Address(display_name=a.get("name") or "", addr_spec=a["email"]) for a in to]
    if cc:
        msg["Cc"] = [Address(display_name=a.get("name") or "", addr_spec=a["email"]) for a in cc]
    if reply_to:
        msg["Reply-To"] = [Address(display_name=a.get("name") or "", addr_spec=a["email"]) for a in reply_to]
    msg["Subject"] = subject
    msg["Date"] = format_datetime(date or datetime.now(UTC))
    msg["Message-ID"] = message_id
    for name, value in headers.items():
        msg[name] = value

    plain = text if text is not None else (html_to_text(markup) if markup else "")
    msg.set_content(plain)
    related = [a for a in attachments if a.inline and a.content_id and markup is not None]
    if markup is not None:
        msg.add_alternative(markup, subtype="html")
        if related:
            html_part = msg.get_payload()[-1]
            for att in related:
                maintype, _, subtype = att.content_type.partition("/")
                html_part.add_related(
                    att.data,
                    maintype=maintype,
                    subtype=subtype or "octet-stream",
                    cid=f"<{att.content_id}>",
                    filename=att.filename,
                    disposition="inline",
                )
    for att in attachments:
        if att in related:
            continue
        maintype, _, subtype = att.content_type.partition("/")
        msg.add_attachment(att.data, maintype=maintype, subtype=subtype or "octet-stream", filename=att.filename)
    return msg
