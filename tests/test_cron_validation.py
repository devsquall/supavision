"""Verify cron expressions are validated at the form boundary.

Before this fix, `POST /resources/{id}/schedule` and `POST /resources` (wizard
final submit) accepted any string as a cron, persisted it, and let the
scheduler discover the typo at the next tick — at which point the user got
no feedback, just a silently-skipped schedule.

The fix: croniter.is_valid() before save. Returns 400 with a structured
error message that tells the user how to format a cron expression.

CLI's `set-schedule` already validated. These tests pin the dashboard
behavior so a future refactor doesn't lose it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_cron.db")


@pytest.fixture
def authed(db_path):
    s = Store(db_path)
    user = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin",
    )
    s.create_user(user)
    sess = Session(user_id=user.id)
    s.create_session(sess)
    # Pre-create a resource we can target.
    r = Resource(name="prod", resource_type="server")
    s.save_resource(r)
    s.close()
    return {"session_id": sess.id, "resource_id": r.id, "csrf": sess.csrf_token}


@pytest.fixture
def client(db_path, authed):
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", authed["session_id"])
        yield c


def test_valid_cron_is_accepted(client, authed):
    r = client.post(
        f"/resources/{authed['resource_id']}/schedule",
        data={
            "health_cron": "0 */6 * * *",
            "discovery_cron": "@daily",
            "csrf_token": authed["csrf"],
        },
        headers={"x-csrf-token": authed["csrf"]},
    )
    assert r.status_code == 204, r.text


def test_invalid_health_cron_returns_400_with_message(client, authed):
    r = client.post(
        f"/resources/{authed['resource_id']}/schedule",
        data={
            "health_cron": "garbage * * *",
            "discovery_cron": "",
            "csrf_token": authed["csrf"],
        },
        headers={"x-csrf-token": authed["csrf"]},
    )
    assert r.status_code == 400
    body = r.json()
    assert "Health-check cron expression" in body["detail"]
    assert "garbage * * *" in body["detail"]


def test_invalid_discovery_cron_returns_400(client, authed):
    r = client.post(
        f"/resources/{authed['resource_id']}/schedule",
        data={
            "health_cron": "",
            "discovery_cron": "not-cron",
            "csrf_token": authed["csrf"],
        },
        headers={"x-csrf-token": authed["csrf"]},
    )
    assert r.status_code == 400
    assert "Discovery cron expression" in r.json()["detail"]


def test_empty_cron_strings_are_treated_as_no_schedule(client, authed):
    """User clearing both fields removes both schedules — no 400."""
    r = client.post(
        f"/resources/{authed['resource_id']}/schedule",
        data={
            "health_cron": "",
            "discovery_cron": "",
            "csrf_token": authed["csrf"],
        },
        headers={"x-csrf-token": authed["csrf"]},
    )
    assert r.status_code == 204


def test_shortcut_cron_strings_accepted(client, authed):
    """@hourly, @daily, @weekly all valid via croniter."""
    for expr in ("@hourly", "@daily", "@weekly"):
        r = client.post(
            f"/resources/{authed['resource_id']}/schedule",
            data={
                "health_cron": expr,
                "discovery_cron": "",
                "csrf_token": authed["csrf"],
            },
            headers={"x-csrf-token": authed["csrf"]},
        )
        assert r.status_code == 204, f"{expr} rejected: {r.text}"
