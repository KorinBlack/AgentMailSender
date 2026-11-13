from __future__ import annotations

import pytest

from agentmailsender.errors import ApiError
from agentmailsender.mail import (
    OutgoingAttachment,
    apply_signature,
    build_message,
    html_to_text,
    normalize_address,
    validate_email,
    validate_headers,
)


def test_address_forms():
    assert normalize_address("jane@Example.ORG", field="to") == {"email": "jane@example.org", "name": ""}
    assert normalize_address("Jane Doe <jane@example.org>", field="to") == {"email": "jane@example.org", "name": "Jane Doe"}
    assert normalize_address({"email": "a@b.co", "name": "Ä Ö"}, field="to") == {"email": "a@b.co", "name": "Ä Ö"}
    assert validate_email("info@bücher.de") == "info@xn--bcher-kva.de"
    for bad in ["no-at-sign", "a@b", "two@@example.org", "a b@example.org", ".dot@example.org", "x@-bad-.com"]:
        with pytest.raises(ApiError):
            validate_email(bad)
    with pytest.raises(ApiError):
        normalize_address("Evil\r\nBcc: x@y.z <a@b.co>", field="to")


def test_header_allowlist():
    assert validate_headers({"in-reply-to": "<a@b>", "X-Ticket": "42"}) == {"In-Reply-To": "<a@b>", "X-Ticket": "42"}
    for name in ["From", "Bcc", "Subject", "Return-Path", "Content-Type"]:
        with pytest.raises(ApiError):
            validate_headers({name: "x"})


def test_html_to_text():
    text = html_to_text(
        "<style>p{color:red}</style><h1>Title</h1><p>Hello <b>world</b>,<br>see <a href='https://x.org/a'>this link</a>.</p>"
        "<ul><li>one</li><li>two</li></ul>"
    )
    assert text == "Title\n\nHello world,\nsee this link (https://x.org/a).\n\n- one\n- two"


def test_signature_goes_before_closing_body():
    text, markup = apply_signature("Hi", "<html><body><p>Hi</p></body></html>", {"text": "Bob", "html": ""})
    assert text == "Hi\n\n-- \nBob"
    assert markup.endswith('<div class="signature" style="margin-top:16px">Bob</div></body></html>')
    assert apply_signature("Hi", None, None) == ("Hi", None)


def test_build_message_structure():
    msg = build_message(
        from_email="bot@example.com",
        from_name="Bot, Inc.",
        to=[{"email": "a@example.org", "name": "Ünïcode Name"}],
        cc=[],
        reply_to=[],
        subject="Grüße",
        text=None,
        markup='<p>Hi <img src="cid:logo"></p>',
        headers={"X-Ticket": "42"},
        message_id="<id@example.com>",
        attachments=[
            OutgoingAttachment("logo.png", "image/png", b"png", inline=True, content_id="logo"),
            OutgoingAttachment("data.csv", "text/csv", b"a,b\n1,2\n"),
        ],
    )
    raw = msg.as_bytes()
    assert b'From: "Bot, Inc." <bot@example.com>' in raw
    assert b"=?utf-8?" in raw  # encoded subject / display name
    assert msg.get_content_type() == "multipart/mixed"
    types = [part.get_content_type() for part in msg.walk()]
    assert types == ["multipart/mixed", "multipart/alternative", "text/plain", "multipart/related", "text/html", "image/png", "text/csv"]
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Hi"
