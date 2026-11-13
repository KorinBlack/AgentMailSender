#!/usr/bin/env python3
"""Command-line client for the AgentMailSender approval gateway (standard library only).

Configuration comes from the environment:
  AGENT_MAIL_URL      base URL of the gateway, e.g. https://mail-gate.example.com
  AGENT_MAIL_API_KEY  the profile's API key (ams_...)

Every command prints JSON on stdout. Errors go to stderr with a non-zero exit code.

Examples:
  mailgate.py profile
  mailgate.py send --to "Jane <jane@example.org>" --subject "Quote" --text-file body.txt \
      --attach quote.pdf --comment "Jane asked for a quote in ticket 1042." --idempotency-key ticket-1042
  mailgate.py status 3f2a...
  mailgate.py wait 3f2a... --timeout 900
  mailgate.py list --status rejected,failed
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FINAL_EXIT_CODES = {"sent": 0, "rejected": 2, "failed": 3, "cancelled": 4}


class GatewayError(Exception):
    pass


def _config() -> tuple[str, str]:
    url = os.environ.get("AGENT_MAIL_URL", "").strip().rstrip("/")
    key = os.environ.get("AGENT_MAIL_API_KEY", "").strip()
    if not url or not key:
        raise GatewayError("Set AGENT_MAIL_URL and AGENT_MAIL_API_KEY in the environment.")
    return url, key


def call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    base, key = _config()
    url = f"{base}{path}"
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "mailgate-cli/1.0")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {"detail": raw.decode(errors="replace")[:500]}
        message = detail.get("detail") or f"HTTP {exc.code}"
        code = detail.get("code")
        raise GatewayError(f"{exc.code} {code + ': ' if code else ''}{message}") from None
    except urllib.error.URLError as exc:
        raise GatewayError(f"Cannot reach {base}: {exc.reason}") from None


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _attachment(spec: str, inline: bool = False) -> dict:
    """``path`` or ``path=content-id`` (inline images)."""
    content_id = None
    path = spec
    if inline:
        if "=" not in spec:
            raise GatewayError(f"--inline expects PATH=CONTENT_ID, got {spec!r}")
        path, content_id = spec.rsplit("=", 1)
    if not os.path.isfile(path):
        raise GatewayError(f"Attachment not found: {path}")
    with open(path, "rb") as handle:
        content = base64.b64encode(handle.read()).decode()
    item = {
        "filename": os.path.basename(path),
        "content_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "content_base64": content,
    }
    if inline:
        item.update(inline=True, content_id=content_id)
    return item


def _pairs(values: list[str] | None, what: str) -> dict:
    out = {}
    for value in values or []:
        if "=" not in value:
            raise GatewayError(f"{what} expects KEY=VALUE, got {value!r}")
        key, val = value.split("=", 1)
        out[key.strip()] = val
    return out


def cmd_send(args: argparse.Namespace) -> dict:
    payload: dict = {}
    if args.json:
        payload.update(json.loads(_read(args.json)))
    for name in ("to", "cc", "bcc", "reply_to"):
        values = getattr(args, name)
        if values:
            payload[name] = values
    if args.sender:
        payload["from"] = args.sender
    if args.subject is not None:
        payload["subject"] = args.subject
    if args.text is not None:
        payload["text"] = args.text
    if args.text_file:
        payload["text"] = _read(args.text_file)
    if args.html_file:
        payload["html"] = _read(args.html_file)
    if args.no_signature:
        payload["signature"] = False
    elif args.signature:
        payload["signature"] = args.signature
    if args.comment is not None:
        payload["comment"] = args.comment
    if args.comment_file:
        payload["comment"] = _read(args.comment_file).strip()
    if args.in_reply_to:
        payload["in_reply_to"] = args.in_reply_to
    if args.references:
        payload["references"] = args.references
    headers = _pairs(args.header, "--header")
    if headers:
        payload["headers"] = {**payload.get("headers", {}), **headers}
    metadata = _pairs(args.meta, "--meta")
    if metadata:
        payload["metadata"] = {**payload.get("metadata", {}), **metadata}
    attachments = [_attachment(p) for p in args.attach or []] + [_attachment(p, inline=True) for p in args.inline or []]
    if attachments:
        payload["attachments"] = payload.get("attachments", []) + attachments
    if args.idempotency_key:
        payload["idempotency_key"] = args.idempotency_key
    if args.supersedes:
        payload["supersedes"] = args.supersedes
    for required in ("subject", "comment"):
        if not payload.get(required):
            raise GatewayError(f"--{required} is required.")
    if not (payload.get("to") or payload.get("cc") or payload.get("bcc")):
        raise GatewayError("Give at least one recipient with --to, --cc or --bcc.")
    if payload.get("text") is None and payload.get("html") is None:
        raise GatewayError("Give a body with --text, --text-file or --html-file.")
    return call("POST", "/api/v1/mails", payload)


def cmd_wait(args: argparse.Namespace) -> tuple[dict, int]:
    deadline = time.monotonic() + args.timeout
    while True:
        result = call("GET", f"/api/v1/mails/{args.id}")
        if result.get("final") or result.get("status") == "failed":
            return result, FINAL_EXIT_CODES.get(result["status"], 1)
        if time.monotonic() >= deadline:
            return result, 5
        time.sleep(max(1, min(args.interval, deadline - time.monotonic())))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit and track e-mails through AgentMailSender.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profile", help="Show sender addresses, signatures and limits.")

    send = sub.add_parser("send", help="Submit an e-mail for human approval.")
    send.add_argument("--to", action="append", help='Recipient, "addr" or "Name <addr>". Repeat for more.')
    send.add_argument("--cc", action="append")
    send.add_argument("--bcc", action="append")
    send.add_argument("--reply-to", dest="reply_to", action="append")
    send.add_argument("--from", dest="sender", help="One of the profile's sender addresses.")
    send.add_argument("--subject")
    send.add_argument("--text", help="Plain-text body.")
    send.add_argument("--text-file", help="Read the plain-text body from a file ('-' for stdin).")
    send.add_argument("--html-file", help="Read the HTML body from a file.")
    send.add_argument("--signature", help="Signature key from the profile (default: the profile default).")
    send.add_argument("--no-signature", action="store_true", help="Send without a signature.")
    send.add_argument("--attach", action="append", help="Attach a file. Repeat for more.")
    send.add_argument("--inline", action="append", help="Inline image as PATH=CONTENT_ID (use src='cid:CONTENT_ID').")
    send.add_argument("--comment", help="For the reviewer: why this e-mail should be sent.")
    send.add_argument("--comment-file", help="Read the comment from a file.")
    send.add_argument("--in-reply-to", help="Message-ID being answered (threading).")
    send.add_argument("--references", help="References header (threading).")
    send.add_argument("--header", action="append", help="Extra header KEY=VALUE (X-* and threading headers).")
    send.add_argument("--meta", action="append", help="Metadata KEY=VALUE echoed back in status responses.")
    send.add_argument("--idempotency-key", help="Same key = same request; makes retries safe.")
    send.add_argument("--supersedes", help="ID of an earlier request this one replaces.")
    send.add_argument("--json", help="Read a full JSON payload from a file ('-' for stdin); flags override it.")

    status = sub.add_parser("status", help="Show one request.")
    status.add_argument("id")

    wait = sub.add_parser("wait", help="Wait until a request is sent, rejected, failed or cancelled.")
    wait.add_argument("id")
    wait.add_argument("--timeout", type=float, default=600, help="Seconds (default 600).")
    wait.add_argument("--interval", type=float, default=15, help="Polling interval in seconds (default 15).")

    listing = sub.add_parser("list", help="List this profile's requests, most recently updated first.")
    listing.add_argument("--status", help="Comma-separated: pending,sent,rejected,failed,cancelled,sending")
    listing.add_argument("--since", help="Only requests updated after this ISO timestamp.")
    listing.add_argument("--limit", type=int, default=20)

    cancel = sub.add_parser("cancel", help="Withdraw a pending request.")
    cancel.add_argument("id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code = 0
    try:
        if args.command == "profile":
            result = call("GET", "/api/v1/profile")
        elif args.command == "send":
            result = cmd_send(args)
        elif args.command == "status":
            result = call("GET", f"/api/v1/mails/{args.id}")
        elif args.command == "wait":
            result, exit_code = cmd_wait(args)
        elif args.command == "list":
            result = call("GET", "/api/v1/mails", query={"status": args.status, "updated_since": args.since, "limit": args.limit})
        elif args.command == "cancel":
            result = call("POST", f"/api/v1/mails/{args.id}/cancel", {})
        else:  # pragma: no cover
            raise GatewayError(f"Unknown command {args.command}")
    except GatewayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
