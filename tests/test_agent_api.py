from __future__ import annotations

from conftest import b64, make_profile, submit


def test_requires_a_valid_api_key(client, agent, admin):
    response = client.post("/api/v1/mails", json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    bad = client.get("/api/v1/profile", headers={"Authorization": "Bearer ams_000000000000_nope"})
    assert bad.status_code == 401

    assert client.get("/api/v1/profile", headers={"X-API-Key": agent.key}).status_code == 200

    admin.delete(f"/api/admin/profiles/{agent.id}/keys/{agent.key_id}")
    assert client.get("/api/v1/profile", headers=agent.headers).status_code == 401


def test_profile_describes_identities_and_signatures(client, agent):
    data = client.get("/api/v1/profile", headers=agent.headers).json()
    assert data["name"] == "support-bot"
    assert [i["email"] for i in data["identities"]] == ["support-bot@example.com", "support-bot-billing@example.com"]
    assert data["signatures"] == [{"key": "default", "label": "Team", "default": True}]
    assert data["limits"]["max_recipients"] == 50


def test_submit_creates_pending_request(client, agent):
    response = submit(client, agent, cc=[{"email": "Team@Example.org", "name": "Team"}], bcc=["audit@example.org"])
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "pending"
    assert data["final"] is False
    assert data["from"] == {"email": "support-bot@example.com", "name": "Support"}
    assert data["to"] == [{"email": "jane@example.org", "name": "Jane Doe"}]
    assert data["cc"] == [{"email": "Team@example.org", "name": "Team"}]
    assert data["message_id"].endswith("@example.com>")

    status = client.get(f"/api/v1/mails/{data['id']}", headers=agent.headers).json()
    assert status["status"] == "pending"
    assert status["decision_comment"] is None


def test_default_signature_is_applied(client, agent, admin):
    data = submit(client, agent, html="<html><body><p>Hello</p></body></html>").json()
    detail = admin.get(f"/api/admin/requests/{data['id']}").json()
    assert detail["text"].endswith("\n\n-- \nThe support team")
    body = admin.get(f"/api/admin/requests/{data['id']}/body.html").text
    assert body.index("The <b>support</b> team") < body.index("</body>")

    plain = submit(client, agent, signature=False).json()
    assert admin.get(f"/api/admin/requests/{plain['id']}").json()["text"] == "Hi Jane,\n\nthe quote is attached."

    unknown = submit(client, agent, signature="missing")
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_signature"


def test_sender_must_be_a_profile_identity(client, agent):
    ok = submit(client, agent, **{"from": "support-bot-billing@example.com"})
    assert ok.status_code == 202
    assert ok.json()["from"]["name"] == "Billing"

    refused = submit(client, agent, **{"from": "ceo@example.com"})
    assert refused.status_code == 422
    assert refused.json()["code"] == "sender_not_allowed"


def test_validation_errors_are_explicit(client, agent):
    cases = [
        ({"to": [], "cc": [], "bcc": []}, "no_recipients"),
        ({"to": ["not-an-address"]}, "invalid_address"),
        ({"subject": "Line\nbreak"}, "invalid_subject"),
        ({"text": None}, "no_body"),
        ({"headers": {"Bcc": "spy@example.org"}}, "header_not_allowed"),
        ({"headers": {"X-Ticket": "1\r\nBcc: spy@example.org"}}, "invalid_header"),
        ({"body": "typo in the field name"}, "validation_error"),
        ({"comment": ""}, "validation_error"),
    ]
    for overrides, code in cases:
        response = submit(client, agent, **overrides)
        assert response.status_code == 422, (overrides, response.text)
        assert response.json()["code"] == code, (overrides, response.json())


def test_idempotency_key_returns_the_existing_request(client, agent):
    first = submit(client, agent, idempotency_key="ticket-1042")
    second = submit(client, agent, idempotency_key="ticket-1042", subject="Something else")
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["subject"] == "Quote for order 1042"


def test_attachment_size_limit(client, agent, admin):
    admin.put("/api/admin/settings", json={"dashboard_url": "", "max_attachments_mb": 1, "max_recipients": 50})
    response = submit(client, agent, attachments=[{"filename": "big.bin", "content_base64": b64(b"0" * (1024 * 1024 + 1))}])
    assert response.status_code == 413
    assert response.json()["code"] == "attachments_too_large"

    bad = submit(client, agent, attachments=[{"filename": "x.txt", "content_base64": "@@not base64@@"}])
    assert bad.status_code == 422


def test_recipient_limit(client, agent, admin):
    admin.put("/api/admin/settings", json={"dashboard_url": "", "max_attachments_mb": 20, "max_recipients": 2})
    response = submit(client, agent, to=["a@example.org", "b@example.org", "c@example.org"])
    assert response.status_code == 422
    assert response.json()["code"] == "too_many_recipients"


def test_profiles_are_isolated(client, agent, admin):
    other = make_profile(admin, name="sales-bot")
    request_id = submit(client, agent).json()["id"]
    assert client.get(f"/api/v1/mails/{request_id}", headers=other.headers).status_code == 404
    assert client.post(f"/api/v1/mails/{request_id}/cancel", headers=other.headers).status_code == 404
    assert client.get("/api/v1/mails", headers=other.headers).json()["items"] == []


def test_cancel_pending_request(client, agent):
    request_id = submit(client, agent).json()["id"]
    cancelled = client.post(f"/api/v1/mails/{request_id}/cancel", headers=agent.headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = client.post(f"/api/v1/mails/{request_id}/cancel", headers=agent.headers)
    assert again.status_code == 409


def test_supersedes_cancels_the_pending_original(client, agent, admin):
    original = submit(client, agent).json()
    revision = submit(client, agent, supersedes=original["id"], subject="Quote for order 1042 (v2)").json()
    assert revision["supersedes"] == original["id"]
    assert client.get(f"/api/v1/mails/{original['id']}", headers=agent.headers).json()["status"] == "cancelled"
    detail = admin.get(f"/api/admin/requests/{original['id']}").json()
    assert detail["superseded_by"][0]["id"] == revision["id"]


def test_disabled_profile_is_refused(client, agent, admin):
    profile = admin.get(f"/api/admin/profiles/{agent.id}").json()
    profile.update(enabled=False, transport_config={"folder": "x"})
    profile["default_reply_to"] = []
    response = admin.put(f"/api/admin/profiles/{agent.id}", json={k: profile[k] for k in (
        "name", "description", "enabled", "identities", "signatures", "default_signature", "default_reply_to",
        "transport_type", "transport_config")})
    assert response.status_code == 200, response.text
    assert submit(client, agent).status_code == 403


def test_list_filters_by_status_and_update_time(client, agent, admin):
    first = submit(client, agent).json()
    second = submit(client, agent, subject="Second").json()
    admin.post(f"/api/admin/requests/{first['id']}/reject", json={"comment": "No"})

    rejected = client.get("/api/v1/mails?status=rejected", headers=agent.headers).json()["items"]
    assert [r["id"] for r in rejected] == [first["id"]]

    since = client.get(f"/api/v1/mails/{second['id']}", headers=agent.headers).json()["updated_at"]
    changed = client.get("/api/v1/mails", params={"updated_since": since}, headers=agent.headers).json()["items"]
    assert [r["id"] for r in changed] == [first["id"]]

    assert client.get("/api/v1/mails?status=bogus", headers=agent.headers).status_code == 422
