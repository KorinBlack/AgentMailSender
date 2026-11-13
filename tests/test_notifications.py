from __future__ import annotations

import hashlib
import hmac
import json

from conftest import make_profile, submit


def _channel(admin, **overrides):
    body = {"name": "hook", "type": "webhook", "config": {"url": "https://hooks.example.com/mail"}, "events": ["request.created"]}
    body.update(overrides)
    response = admin.post("/api/admin/channels", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_webhook_is_signed_and_links_to_the_dashboard(client, agent, admin, hooks):
    admin.put("/api/admin/settings", json={"dashboard_url": "https://review.example.com/", "max_attachments_mb": 20, "max_recipients": 50})
    channel = _channel(admin, config={"url": "https://hooks.example.com/mail", "signing_secret": "s3cret", "headers": '{"X-Team": "ops"}'})
    assert "signing_secret" not in channel["config"]
    assert sorted(channel["config"]["__secrets_set__"]) == ["headers", "signing_secret"]

    request_id = submit(client, agent).json()["id"]
    [call] = hooks.requests
    body = call.content
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert call.headers["X-AgentMailSender-Signature"] == expected
    assert call.headers["X-AgentMailSender-Event"] == "request.created"
    assert call.headers["X-Team"] == "ops"
    payload = json.loads(body)
    assert payload["dashboard_url"] == f"https://review.example.com/#/inbox/{request_id}"
    assert payload["request"]["subject"] == "Quote for order 1042"
    assert payload["request"]["profile"] == "support-bot"


def test_discord_embed(client, agent, admin, hooks):
    _channel(admin, type="discord", config={"webhook_url": "https://discord.com/api/webhooks/1/abc", "mention": "@here"})
    submit(client, agent, subject="Contract renewal")
    [call] = hooks.requests
    assert str(call.url) == "https://discord.com/api/webhooks/1/abc"
    payload = json.loads(call.content)
    assert payload["content"].startswith("@here")
    embed = payload["embeds"][0]
    assert embed["title"] == "Contract renewal"
    assert {"name": "Profile", "value": "support-bot", "inline": True} in embed["fields"]


def test_events_and_profile_filters(client, agent, admin, hooks):
    other = make_profile(admin, name="sales-bot")
    _channel(admin, events=["request.rejected"], profile_ids=[other.id])
    first = submit(client, agent).json()["id"]
    admin.post(f"/api/admin/requests/{first}/reject", json={"comment": "no"})
    assert hooks.requests == []

    second = submit(client, other).json()["id"]
    assert hooks.requests == []
    admin.post(f"/api/admin/requests/{second}/reject", json={"comment": "Rewrite the intro."})
    [call] = hooks.requests
    payload = json.loads(call.content)
    assert payload["event"] == "request.rejected"
    assert payload["request"]["decision_comment"] == "Rewrite the intro."


def test_failures_are_recorded_on_the_channel(client, agent, admin, hooks):
    channel = _channel(admin)
    hooks.status = 500
    submit(client, agent)
    state = admin.get(f"/api/admin/channels/{channel['id']}").json()
    assert state["last_status"] == "error"
    assert "HTTP 500" in state["last_error"]

    hooks.status = 200
    result = admin.post(f"/api/admin/channels/{channel['id']}/test").json()
    assert result["ok"] is True
    assert admin.get(f"/api/admin/channels/{channel['id']}").json()["last_status"] == "ok"


def test_body_template_escapes_json(client, agent, admin, hooks):
    _channel(admin, config={"url": "https://hooks.example.com/teams", "body_template": '{"text": "{{headline}}: {{subject}} by {{profile}}"}'})
    submit(client, agent, subject='Say "hello" \\ goodbye')
    payload = json.loads(hooks.requests[0].content)
    assert payload == {"text": 'New mail request awaiting approval: Say "hello" \\ goodbye by support-bot'}


def test_channel_validation(admin):
    assert admin.post("/api/admin/channels", json={"name": "x", "type": "pigeon"}).status_code == 422
    assert admin.post("/api/admin/channels", json={"name": "x", "type": "webhook", "config": {}}).status_code == 422
    bad_event = admin.post("/api/admin/channels", json={"name": "x", "type": "webhook", "config": {"url": "https://a.example"}, "events": ["nope"]})
    assert bad_event.status_code == 422
    bad_json = admin.post("/api/admin/channels", json={"name": "x", "type": "webhook", "config": {"url": "https://a.example", "headers": "{not json"}})
    assert bad_json.status_code == 422


def test_telegram_slack_and_ntfy_payloads(client, agent, admin, hooks):
    _channel(admin, name="tg", type="telegram", config={"bot_token": "123:abc", "chat_id": "-100", "thread_id": 7})
    _channel(admin, name="slack", type="slack", config={"webhook_url": "https://hooks.slack.com/services/T/B/X"})
    _channel(admin, name="ntfy", type="ntfy", config={"topic": "mail", "access_token": "tok"})
    submit(client, agent, subject="<b>Offer</b> & more")
    calls = {req.url.host: req for req in hooks.requests}

    telegram = json.loads(calls["api.telegram.org"].content)
    assert calls["api.telegram.org"].url.path == "/bot123:abc/sendMessage"
    assert "&lt;b&gt;Offer&lt;/b&gt; &amp; more" in telegram["text"]
    assert telegram["message_thread_id"] == 7

    slack = json.loads(calls["hooks.slack.com"].content)
    assert "&lt;b&gt;Offer&lt;/b&gt; &amp; more" in slack["blocks"][0]["text"]["text"]

    ntfy = calls["ntfy.sh"]
    assert ntfy.headers["Authorization"] == "Bearer tok"
    assert json.loads(ntfy.content)["topic"] == "mail"
