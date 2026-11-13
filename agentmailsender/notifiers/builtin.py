"""Built-in notification channels: Discord, Slack, Telegram, ntfy and a generic webhook."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re

import httpx

from ..fields import Field
from ..utils import truncate
from .base import Event, Notifier, NotifierError, register

_COLORS = {
    "request.created": 0xB7791F,
    "request.sent": 0x1E7B4F,
    "request.failed": 0xC4242A,
    "request.rejected": 0xC4242A,
    "request.cancelled": 0x6B6E72,
}


def _recipients(event: Event, limit: int = 300) -> str:
    to = event.request.get("to") or []
    extra = len(event.request.get("cc") or []) + int(event.request.get("bcc_count") or 0)
    text = ", ".join(to) or "—"
    if extra:
        text += f" (+{extra} cc/bcc)"
    return truncate(text, limit)


def _detail_line(event: Event) -> tuple[str, str] | None:
    r = event.request
    if event.type == "request.rejected" and r.get("decision_comment"):
        return "Operator note", r["decision_comment"]
    if event.type == "request.failed" and r.get("error"):
        return "Error", r["error"]
    return None


@register
class DiscordNotifier(Notifier):
    type = "discord"
    label = "Discord"
    description = "Posts an embed to a Discord channel through an incoming webhook."
    fields = [
        Field(
            "webhook_url",
            "Webhook URL",
            type="url",
            required=True,
            secret=True,
            placeholder="https://discord.com/api/webhooks/…",
            help="Channel settings → Integrations → Webhooks → Copy webhook URL.",
        ),
        Field("username", "Bot name", default="AgentMailSender"),
        Field("mention", "Mention", placeholder="@here or <@&role-id>", help="Optional text placed before the message."),
    ]

    def send(self, event: Event) -> None:
        r = event.request
        embed_fields = [
            {"name": "Profile", "value": truncate(r.get("profile") or "—", 1024), "inline": True},
            {"name": "From", "value": truncate(r.get("from") or "—", 1024), "inline": True},
            {"name": "To", "value": _recipients(event, 1024), "inline": False},
        ]
        detail = _detail_line(event)
        if detail:
            embed_fields.append({"name": detail[0], "value": truncate(detail[1], 1024), "inline": False})
        if event.url:
            embed_fields.append({"name": "Review", "value": f"[Open in dashboard]({event.url})", "inline": False})
        embed = {
            "title": truncate(r.get("subject") or "(no subject)", 256),
            "color": _COLORS.get(event.type, 0x6B6E72),
            "fields": embed_fields,
            "timestamp": event.occurred_at,
            "footer": {"text": f"Request {r.get('id', '')[:8]}"},
        }
        if r.get("comment"):
            embed["description"] = truncate(f"**Agent note:** {r['comment']}", 2000)
        if event.url:
            embed["url"] = event.url
        mention = (self.config.get("mention") or "").strip()
        payload = {
            "username": self.config.get("username") or "AgentMailSender",
            "content": f"{mention} **{event.headline}**".strip(),
            "embeds": [embed],
            "allowed_mentions": {"parse": ["everyone", "roles", "users"] if mention else []},
        }
        self._post_json(self.config["webhook_url"], payload)


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@register
class SlackNotifier(Notifier):
    type = "slack"
    label = "Slack"
    description = "Posts a message through a Slack incoming webhook (also works with Mattermost)."
    fields = [
        Field(
            "webhook_url",
            "Webhook URL",
            type="url",
            required=True,
            secret=True,
            placeholder="https://hooks.slack.com/services/…",
        ),
    ]

    def send(self, event: Event) -> None:
        r = event.request
        subject = _slack_escape(r.get("subject") or "(no subject)")
        title = f"<{event.url}|{subject}>" if event.url else subject
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_slack_escape(event.headline)}*\n{title}"}},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Profile *{_slack_escape(r.get('profile') or '—')}* · to {_slack_escape(_recipients(event))}",
                    }
                ],
            },
        ]
        if r.get("comment"):
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "> " + _slack_escape(truncate(r["comment"], 1500)).replace("\n", "\n> ")}}
            )
        detail = _detail_line(event)
        if detail:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{detail[0]}:* {_slack_escape(truncate(detail[1], 1500))}"}})
        payload = {"text": f"{event.headline}: {r.get('subject') or ''}", "blocks": blocks}
        self._post_json(self.config["webhook_url"], payload)


@register
class TelegramNotifier(Notifier):
    type = "telegram"
    label = "Telegram"
    description = "Sends a message with a Telegram bot to a chat, group or channel."
    fields = [
        Field("bot_token", "Bot token", type="password", required=True, secret=True, help="From @BotFather."),
        Field("chat_id", "Chat ID", required=True, placeholder="-1001234567890"),
        Field("thread_id", "Topic ID", type="number", help="Optional: forum topic (message_thread_id)."),
        Field("api_url", "API base URL", type="url", default="https://api.telegram.org"),
    ]

    def send(self, event: Event) -> None:
        r = event.request
        esc = html.escape
        lines = [
            f"<b>{esc(event.headline)}</b>",
            esc(r.get("subject") or "(no subject)"),
            f"Profile: {esc(r.get('profile') or '—')} · To: {esc(_recipients(event))}",
        ]
        if r.get("comment"):
            lines += ["", f"<i>{esc(truncate(r['comment'], 1500))}</i>"]
        detail = _detail_line(event)
        if detail:
            lines += ["", f"<b>{esc(detail[0])}:</b> {esc(truncate(detail[1], 1000))}"]
        if event.url:
            lines += ["", f'<a href="{esc(event.url, quote=True)}">Open in dashboard</a>']
        payload = {
            "chat_id": self.config["chat_id"],
            "text": truncate("\n".join(lines), 4000),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self.config.get("thread_id"):
            payload["message_thread_id"] = int(self.config["thread_id"])
        base = (self.config.get("api_url") or "https://api.telegram.org").rstrip("/")
        self._post_json(f"{base}/bot{self.config['bot_token']}/sendMessage", payload)


@register
class NtfyNotifier(Notifier):
    type = "ntfy"
    label = "ntfy"
    description = "Push notification to phones and desktops through ntfy.sh or a self-hosted ntfy server."
    fields = [
        Field("server_url", "Server", type="url", default="https://ntfy.sh"),
        Field("topic", "Topic", required=True, placeholder="my-mail-approvals"),
        Field("access_token", "Access token", type="password", secret=True, help="Only for protected topics."),
        Field(
            "priority",
            "Priority",
            type="select",
            default="3",
            options=[("1", "Min"), ("2", "Low"), ("3", "Default"), ("4", "High"), ("5", "Urgent")],
        ),
    ]

    def send(self, event: Event) -> None:
        r = event.request
        body = [r.get("subject") or "(no subject)", f"Profile: {r.get('profile') or '—'} · To: {_recipients(event)}"]
        if r.get("comment"):
            body += ["", truncate(r["comment"], 1000)]
        detail = _detail_line(event)
        if detail:
            body += ["", f"{detail[0]}: {truncate(detail[1], 800)}"]
        payload = {
            "topic": self.config["topic"],
            "title": event.headline,
            "message": "\n".join(body),
            "tags": ["envelope"],
            "priority": int(self.config.get("priority") or 3),
        }
        if event.url:
            payload["click"] = event.url
            payload["actions"] = [{"action": "view", "label": "Open", "url": event.url}]
        headers = {}
        if self.config.get("access_token"):
            headers["Authorization"] = f"Bearer {self.config['access_token']}"
        self._post_json((self.config.get("server_url") or "https://ntfy.sh").rstrip("/"), payload, headers=headers)


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def render_template(template: str, event: Event, *, json_escape: bool) -> str:
    values = event.placeholders()

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is None:
            return match.group(0)
        return json.dumps(value)[1:-1] if json_escape else value

    return _PLACEHOLDER_RE.sub(replace, template)


@register
class WebhookNotifier(Notifier):
    type = "webhook"
    label = "Generic webhook"
    description = (
        "Calls any HTTP endpoint. Sends a JSON event by default, or your own body template for services "
        "such as Microsoft Teams, Gotify, Pushover or an agent's callback URL."
    )
    fields = [
        Field("url", "URL", type="url", required=True, placeholder="https://example.com/hooks/mail"),
        Field("method", "Method", type="select", default="POST", options=[("POST", "POST"), ("PUT", "PUT")]),
        Field(
            "headers",
            "Extra headers",
            type="json",
            secret=True,
            placeholder='{"Authorization": "Bearer …"}',
            help="JSON object. Stored encrypted.",
        ),
        Field(
            "signing_secret",
            "Signing secret",
            type="password",
            secret=True,
            help="When set, the body is signed with HMAC-SHA256 in the X-AgentMailSender-Signature header.",
        ),
        Field("content_type", "Content type", default="application/json"),
        Field(
            "body_template",
            "Body template",
            type="textarea",
            placeholder='{"text": "{{headline}}: {{subject}} ({{profile}}) {{url}}"}',
            help=(
                "Optional. Placeholders: {{event}} {{headline}} {{subject}} {{profile}} {{from}} {{to}} {{comment}} "
                "{{decision_comment}} {{error}} {{status}} {{id}} {{url}} {{occurred_at}}. Values are JSON-escaped "
                "when the content type is JSON. Leave empty to send the standard JSON event."
            ),
        ),
    ]

    def send(self, event: Event) -> None:
        content_type = self.config.get("content_type") or "application/json"
        template = self.config.get("body_template")
        if template:
            body = render_template(template, event, json_escape="json" in content_type.lower())
        else:
            body = json.dumps(event.payload(), ensure_ascii=False)
        data = body.encode()
        headers = {"Content-Type": content_type, "X-AgentMailSender-Event": event.type}
        secret = self.config.get("signing_secret")
        if secret:
            headers["X-AgentMailSender-Signature"] = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
        extra = self.config.get("headers") or {}
        if not isinstance(extra, dict):
            raise NotifierError("Extra headers must be a JSON object.")
        headers.update({str(k): str(v) for k, v in extra.items()})
        try:
            response = self.http.request(self.config.get("method") or "POST", self.config["url"], content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise NotifierError(f"Request failed: {exc}") from exc
        self._check(response)
