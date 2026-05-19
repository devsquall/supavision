"""Verify admin actions (API key create/revoke, resource create/delete) are
written to the audit log so operators can answer "who did what when".

Before this fix, only authentication events were logged. An admin user
could create or revoke API keys and create or delete resources with no
trace — bad for both incident investigation and post-mortem.

The audit log dashboard at /settings/audit-log surfaces these new event
types alongside the existing auth events.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Session, User
from supavision.web.app import create_app
from supavision.web.auth import generate_api_key, hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_admin_audit.db")


def _seed_admin(db_path: str) -> tuple[str, User]:
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
    s.close()
    return sess.id, user


def _login_csrf(db_path: str, session_id: str) -> str:
    s = Store(db_path)
    sess = s.get_session(session_id)
    s.close()
    return sess.csrf_token if sess else ""


def test_api_key_create_writes_audit_event(db_path):
    session_id, user = _seed_admin(db_path)
    csrf = _login_csrf(db_path, session_id)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.post(
            "/settings/api-keys",
            data={"label": "test-key", "csrf_token": csrf},
            headers={"x-csrf-token": csrf},
        )
        assert r.status_code == 303  # redirects to /settings?new_key=...

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="api_key_created")
    s.close()
    assert len(events) == 1
    assert events[0]["user_id"] == user.id
    assert "label='test-key'" in events[0]["detail"]
    assert "role=admin" in events[0]["detail"]


def test_api_key_revoke_writes_audit_event(db_path):
    session_id, user = _seed_admin(db_path)
    csrf = _login_csrf(db_path, session_id)
    # Pre-create a key directly so we can revoke it.
    s = Store(db_path)
    kid, _raw, kh = generate_api_key()
    s.save_api_key(kid, kh, label="to-revoke", role="viewer")
    s.close()

    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.post(
            f"/settings/api-keys/{kid}/revoke",
            data={"csrf_token": csrf},
            headers={"x-csrf-token": csrf},
        )
        assert r.status_code in (200, 303)

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="api_key_revoked")
    s.close()
    assert len(events) == 1
    assert events[0]["user_id"] == user.id
    assert kid in events[0]["detail"]


def test_resource_delete_writes_audit_event(db_path):
    session_id, user = _seed_admin(db_path)
    csrf = _login_csrf(db_path, session_id)
    s = Store(db_path)
    res = Resource(name="prod-doomed", resource_type="server")
    s.save_resource(res)
    s.close()

    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.post(
            f"/resources/{res.id}/delete",
            data={"csrf_token": csrf},
            headers={"x-csrf-token": csrf},
        )
        assert r.status_code == 303

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="resource_deleted")
    s.close()
    assert len(events) == 1
    assert events[0]["user_id"] == user.id
    # The resource ID, type, and name should all be in the detail string so
    # post-mortem can identify what was deleted.
    detail = events[0]["detail"]
    assert res.id in detail
    assert "server" in detail
    assert "prod-doomed" in detail


def test_audit_log_dashboard_shows_admin_action_events(db_path):
    """The /settings/audit-log dropdown must include the new event types."""
    session_id, _ = _seed_admin(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log")
        assert r.status_code == 200
        body = r.text
        for event in [
            "api_key_created",
            "api_key_revoked",
            "resource_created",
            "resource_deleted",
        ]:
            assert event in body, f"missing event in dropdown: {event}"
