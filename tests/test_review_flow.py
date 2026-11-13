from __future__ import annotations

import email
import json
from email import policy

from sqlalchemy import select

from agentmailsender.models import Profile
from conftest import ADMIN_PASSWORD, b64, make_profile, submit


def _outbox(ctx, folder):
    return sorted((ctx.config.outbox_dir / folder).glob("*.eml"))


def test_operator_auth_and_csrf(client, agent):
    # The agent fixture signed the shared client in: start from a signed-out browser.
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get("/api/admin/requests").status_code == 401
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert wrong.status_code == 401
    login = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert login.status_code == 200
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie

    request_id = submit(client, agent).json()["id"]
    assert client.get("/api/admin/requests").status_code == 200
    no_csrf = client.post(f"/api/admin/requests/{request_id}/approve", json={})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf"

    client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert client.get("/api/admin/requests").status_code == 401


def test_login_is_rate_limited(client):
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    blocked = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429


def test_approve_delivers_the_exact_message(client, agent, admin, ctx):
    response = submit(
        client,
        agent,
        cc=["team@example.org"],
        bcc=["audit@example.org"],
        reply_to=["Desk <desk@example.com>"],
        in_reply_to="<original@example.org>",
        html='<p>Hello <img src="cid:logo"></p>',
        attachments=[
            {"filename": "../../etc/quote.pdf", "content_type": "application/pdf", "content_base64": b64(b"%PDF-1.4 demo")},
            {"filename": "logo.png", "content_type": "image/png", "inline": True, "content_id": "logo", "content_base64": b64(b"\x89PNG demo")},
        ],
    )
    request_id = response.json()["id"]
    approved = admin.post(f"/api/admin/requests/{request_id}/approve", json={"comment": "Looks right"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "sent"

    [eml] = _outbox(ctx, "support-bot")
    message = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert message["From"] == "Support <support-bot@example.com>"
    assert message["To"] == "Jane Doe <jane@example.org>"
    assert message["Cc"] == "team@example.org"
    assert message["Reply-To"] == "Desk <desk@example.com>"
    assert message["Bcc"] is None
    assert message["In-Reply-To"] == "<original@example.org>"
    assert message["Message-ID"] == response.json()["message_id"]
    filenames = [part.get_filename() for part in message.iter_attachments()]
    assert filenames == ["quote.pdf"]
    related = [p for p in message.walk() if p.get("Content-ID") == "<logo>"]
    assert len(related) == 1
    envelope = json.loads(eml.with_suffix(".json").read_text())
    assert envelope["recipients"] == ["jane@example.org", "team@example.org", "audit@example.org"]

    feedback = client.get(f"/api/v1/mails/{request_id}", headers=agent.headers).json()
    assert feedback["status"] == "sent"
    assert feedback["final"] is True
    assert feedback["decision_comment"] == "Looks right"
    assert feedback["sent_at"]

    again = admin.post(f"/api/admin/requests/{request_id}/approve", json={})
    assert again.status_code == 409
    assert len(_outbox(ctx, "support-bot")) == 1


def test_reject_sends_feedback_to_the_agent(client, agent, admin, ctx):
    request_id = submit(client, agent).json()["id"]
    rejected = admin.post(f"/api/admin/requests/{request_id}/reject", json={"comment": "Attach the PDF, not a link."})
    assert rejected.json()["status"] == "rejected"
    feedback = client.get(f"/api/v1/mails/{request_id}", headers=agent.headers).json()
    assert feedback["status"] == "rejected"
    assert feedback["decision_comment"] == "Attach the PDF, not a link."
    assert _outbox(ctx, "support-bot") == []
    assert admin.post(f"/api/admin/requests/{request_id}/approve", json={}).status_code == 409


def test_failed_delivery_can_be_retried(client, admin, ctx):
    agent = make_profile(
        admin, name="smtp-bot", transport="smtp",
        config={"host": "127.0.0.1", "port": 1, "security": "none", "timeout": 2},
    )
    request_id = submit(client, agent).json()["id"]
    failed = admin.post(f"/api/admin/requests/{request_id}/approve", json={}).json()
    assert failed["status"] == "failed"
    assert "Could not connect" in failed["error"]
    assert client.get(f"/api/v1/mails/{request_id}", headers=agent.headers).json()["error"]

    profile = admin.get(f"/api/admin/profiles/{agent.id}").json()
    update = {k: profile[k] for k in ("name", "description", "enabled", "identities", "signatures", "default_signature")}
    update.update(default_reply_to=[], transport_type="file", transport_config={"folder": "retry"})
    assert admin.put(f"/api/admin/profiles/{agent.id}", json=update).status_code == 200

    retried = admin.post(f"/api/admin/requests/{request_id}/retry").json()
    assert retried["status"] == "sent"
    assert retried["send_attempts"] == 2
    assert len(_outbox(ctx, "retry")) == 1


def test_body_preview_is_locked_down(client, agent, admin):
    request_id = submit(
        client, agent,
        html='<p>Hi <img src="https://tracker.example.com/p.gif"><img src="cid:logo"></p>',
        attachments=[{"filename": "logo.png", "content_type": "image/png", "inline": True, "content_id": "logo", "content_base64": b64(b"png")}],
    ).json()["id"]
    detail = admin.get(f"/api/admin/requests/{request_id}").json()
    assert detail["has_remote_content"] is True

    preview = admin.get(f"/api/admin/requests/{request_id}/body.html")
    csp = preview.headers["content-security-policy"]
    assert "sandbox" in csp and "allow-scripts" not in csp
    assert "https:" not in csp.split("img-src")[1].split(";")[0]
    attachment_id = detail["attachments"][0]["id"]
    assert f"attachments/{attachment_id}/inline" in preview.text
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"

    remote = admin.get(f"/api/admin/requests/{request_id}/body.html?remote=1")
    assert "https:" in remote.headers["content-security-policy"].split("img-src")[1].split(";")[0]

    inline = admin.get(f"/api/admin/requests/{request_id}/attachments/{attachment_id}/inline")
    assert inline.status_code == 200 and inline.headers["content-type"] == "image/png"


def test_attachment_download_and_eml(client, agent, admin):
    request_id = submit(client, agent, attachments=[{"filename": "notes.html", "content_base64": b64(b"<script>x</script>")}]).json()["id"]
    detail = admin.get(f"/api/admin/requests/{request_id}").json()
    attachment = detail["attachments"][0]
    download = admin.get(f"/api/admin/requests/{request_id}/attachments/{attachment['id']}")
    assert download.content == b"<script>x</script>"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")

    eml = admin.get(f"/api/admin/requests/{request_id}/eml")
    assert eml.headers["content-type"].startswith("message/rfc822")
    assert b"Subject: Quote for order 1042" in eml.content


def test_transport_secrets_are_encrypted_and_never_returned(admin, ctx):
    agent = make_profile(
        admin, name="smtp-secret", transport="smtp",
        config={"host": "smtp.example.com", "username": "bot", "password": "hunter2-very-secret"},
    )
    view = admin.get(f"/api/admin/profiles/{agent.id}").json()
    assert "password" not in view["transport_config"]
    assert view["transport_config"]["__secrets_set__"] == ["password"]
    assert "hunter2" not in json.dumps(view)

    with ctx.session() as db:
        stored = db.scalar(select(Profile).where(Profile.id == agent.id)).transport_config
    assert stored["password"].startswith("enc:v1:")
    assert ctx.secrets.decrypt(stored["password"]) == "hunter2-very-secret"

    update = {k: view[k] for k in ("name", "description", "enabled", "identities", "signatures", "default_signature", "transport_type")}
    update.update(default_reply_to=[], transport_config={"host": "smtp2.example.com", "username": "bot", "password": ""})
    assert admin.put(f"/api/admin/profiles/{agent.id}", json=update).status_code == 200
    with ctx.session() as db:
        stored = db.scalar(select(Profile).where(Profile.id == agent.id)).transport_config
    assert stored["host"] == "smtp2.example.com"
    assert ctx.secrets.decrypt(stored["password"]) == "hunter2-very-secret"


def test_deleting_a_profile_cancels_pending_requests(client, agent, admin):
    request_id = submit(client, agent).json()["id"]
    assert admin.delete(f"/api/admin/profiles/{agent.id}").status_code == 200
    detail = admin.get(f"/api/admin/requests/{request_id}").json()
    assert detail["status"] == "cancelled"
    assert detail["profile_name"] == "support-bot"
    assert client.get("/api/v1/profile", headers=agent.headers).status_code == 401


def test_admin_list_search_and_stats(client, agent, admin):
    submit(client, agent, subject="Invoice 77 overdue")
    submit(client, agent, subject="Welcome aboard")
    found = admin.get("/api/admin/requests", params={"status": "all", "q": "invoice"}).json()
    assert found["total"] == 1 and found["items"][0]["subject"] == "Invoice 77 overdue"
    wildcard = admin.get("/api/admin/requests", params={"status": "all", "q": "%"}).json()
    assert wildcard["total"] == 0
    stats = admin.get("/api/admin/stats").json()
    assert stats["counts"]["pending"] == 2


def test_operator_management(admin, client):
    created = admin.post("/api/admin/operators", json={"username": "alice", "password": "alice-password-1"})
    assert created.status_code == 201
    assert admin.post("/api/admin/operators", json={"username": "alice", "password": "alice-password-1"}).status_code == 409
    me = admin.get("/api/auth/me").json()
    assert admin.delete(f"/api/admin/operators/{me['id']}").status_code == 409
    assert admin.delete(f"/api/admin/operators/{created.json()['id']}").status_code == 200


def test_password_change(admin):
    wrong = admin.post("/api/auth/password", json={"current_password": "nope", "new_password": "a-new-password-1"})
    assert wrong.status_code == 422
    ok = admin.post("/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "a-new-password-1"})
    assert ok.status_code == 200


def test_security_headers_on_the_app(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "default-src 'self'" in index.headers["content-security-policy"]
    assert index.headers["x-frame-options"] == "DENY"


def test_oversized_body_is_refused(client, agent, ctx):
    limit = ctx.config.max_body_mb * 1024 * 1024
    response = client.post(
        "/api/v1/mails", content=b"x" * (limit + 1), headers={**agent.headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 413
