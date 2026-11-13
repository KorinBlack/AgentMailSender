from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agentmailsender.app import create_app
from agentmailsender.config import Config
from agentmailsender.context import AppContext

ADMIN_PASSWORD = "test-password-123"


class HookRecorder:
    """Stands in for every outgoing HTTP call made by notifiers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": self.status < 400})


@pytest.fixture
def hooks() -> HookRecorder:
    return HookRecorder()


@pytest.fixture
def ctx(tmp_path, hooks):
    config = Config(data_dir=tmp_path, admin_password=ADMIN_PASSWORD, secret_key="test-secret-key")
    http = httpx.Client(transport=httpx.MockTransport(hooks.handler))
    return AppContext(config, http_client=http, sync_notifications=True, notification_retry_delays=())


@pytest.fixture
def client(ctx):
    with TestClient(create_app(context=ctx)) as test_client:
        yield test_client


@pytest.fixture
def admin(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def make_profile(admin, name="support-bot", transport="file", config=None, **extra):
    payload = {
        "name": name,
        "identities": [
            {"email": f"{name}@example.com", "name": "Support"},
            {"email": f"{name}-billing@example.com", "name": "Billing"},
        ],
        "signatures": [{"key": "default", "label": "Team", "text": "The support team", "html": "<p>The <b>support</b> team</p>"}],
        "default_signature": "default",
        "transport_type": transport,
        "transport_config": config if config is not None else {"folder": name},
        **extra,
    }
    response = admin.post("/api/admin/profiles", json=payload)
    assert response.status_code == 201, response.text
    profile_id = response.json()["id"]
    key_response = admin.post(f"/api/admin/profiles/{profile_id}/keys", json={"label": "tests"})
    assert key_response.status_code == 201, key_response.text
    key = key_response.json()["key"]
    return SimpleNamespace(
        id=profile_id,
        name=name,
        key=key,
        key_id=key_response.json()["api_key"]["id"],
        headers={"Authorization": f"Bearer {key}"},
    )


@pytest.fixture
def agent(admin):
    return make_profile(admin)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def submit(client, agent, **overrides):
    body = {
        "to": ["Jane Doe <jane@example.org>"],
        "subject": "Quote for order 1042",
        "text": "Hi Jane,\n\nthe quote is attached.",
        "comment": "Jane asked for a quote in ticket 1042.",
    }
    body.update(overrides)
    return client.post("/api/v1/mails", json=body, headers=agent.headers)
