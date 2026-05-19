"""Tests for /settings/audit-log — the auth-events dashboard page.

Before this fix, `auth_audit_log` was written on every login attempt and
account change but never displayed. Operators had to read sqlite by hand to
see "who tried to log in 30 times in the last hour" or "who changed which
user's role yesterday".

The audit log is admin-only. Viewers see 403 — same as `/docs`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_audit.db")


def _seed(db_path: str, role: str = "admin") -> str:
    s = Store(db_path)
    user = User(
        email=f"{role}@test.com",
        password_hash=hash_password("Pass1234!"),
        name=role,
        role=role,
    )
    s.create_user(user)
    sess = Session(user_id=user.id)
    s.create_session(sess)
    # Seed a few audit events for content tests.
    s.log_auth_event("login_success", user_id=user.id, email=user.email, ip_address="10.0.0.1")
    s.log_auth_event("login_failure", email="attacker@evil.com", ip_address="1.2.3.4")
    s.log_auth_event("user_created", user_id=user.id, email=user.email)
    s.close()
    return sess.id


def test_admin_sees_audit_log_page(db_path):
    session_id = _seed(db_path, role="admin")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log")
        assert r.status_code == 200
        body = r.text
        assert "Audit log" in body
        # Each seeded event should be visible.
        assert "login success" in body or "login_success" in body
        assert "login failure" in body or "login_failure" in body
        assert "attacker@evil.com" in body


def test_viewer_gets_403(db_path):
    session_id = _seed(db_path, role="viewer")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log")
        assert r.status_code == 403


def test_unauthenticated_redirects_to_login(db_path):
    _seed(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        # No session cookie set.
        r = c.get("/settings/audit-log")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]


def test_event_filter_narrows_results(db_path):
    session_id = _seed(db_path, role="admin")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log?event=login_failure")
        assert r.status_code == 200
        body = r.text
        # Failure event should be visible, success shouldn't.
        assert "attacker@evil.com" in body
        # The login_success email wasn't "attacker" so easy to distinguish.
        assert "10.0.0.1" not in body


def test_empty_state_when_filter_matches_nothing(db_path):
    session_id = _seed(db_path, role="admin")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log?event=role_changed")
        assert r.status_code == 200
        # No role_changed events seeded — empty state should render.
        assert "No" in r.text and "events" in r.text


def test_settings_page_links_to_audit_log(db_path):
    session_id = _seed(db_path, role="admin")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings")
        assert r.status_code == 200
        assert "/settings/audit-log" in r.text


def test_list_auth_events_paginated_returns_count_and_rows(tmp_path):
    """Unit test for the underlying store method (no HTTP)."""
    db_path = tmp_path / "unit.db"
    s = Store(str(db_path))
    for i in range(15):
        s.log_auth_event("login_success", email=f"u{i}@test.com")
    rows, total = s.list_auth_events_paginated(limit=10, offset=0)
    assert total == 15
    assert len(rows) == 10
    rows2, total2 = s.list_auth_events_paginated(limit=10, offset=10)
    assert total2 == 15
    assert len(rows2) == 5
    s.close()


def test_pagination_links_present_when_more_than_one_page(db_path):
    """50 events per page; seed 75 so we get 2 pages and a "Next" link."""
    session_id = _seed(db_path, role="admin")
    s = Store(db_path)
    for i in range(75):
        s.log_auth_event("login_success", email=f"u{i}@test.com")
    s.close()
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings/audit-log")
        assert r.status_code == 200
        assert "Next" in r.text
        assert "Page 1 of 2" in r.text

        r2 = c.get("/settings/audit-log?page=2")
        assert r2.status_code == 200
        assert "Prev" in r2.text
        assert "Page 2 of 2" in r2.text


def test_list_auth_events_paginated_filter(tmp_path):
    db_path = tmp_path / "unit_filter.db"
    s = Store(str(db_path))
    for _ in range(3):
        s.log_auth_event("login_success", email="ok@test.com")
    for _ in range(2):
        s.log_auth_event("login_failure", email="bad@test.com")
    rows, total = s.list_auth_events_paginated(event="login_failure")
    assert total == 2
    assert all(r["event"] == "login_failure" for r in rows)
    s.close()
