"""Verify that the dashboard is locked down until an admin user is created.

Pre-0.4.5.dev0, when `store.count_users() == 0` the session middleware logged
CRITICAL and served the dashboard with `is_admin=True`. Anyone who hit the
port before `supavision create-admin` ran could browse + mutate as admin.

The fix: middleware redirects to `/landing?setup=1` until at least one user
exists. /landing is the public marketing page (always reachable); the
dashboard chrome is unreachable. /login still works structurally but no
credentials match because the users table is empty.

This test exercises the real `create_app` factory (not the dashboard-only
test app used elsewhere), so the middleware is in the request path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_first_run.db")


@pytest.fixture
def app_empty(db_path):
    """Factory-built app with an empty store (no users)."""
    return create_app(db_path=db_path)


@pytest.fixture
def app_seeded(db_path):
    """Factory-built app with one admin user seeded."""
    s = Store(db_path)
    s.create_user(
        User(
            email="admin@test.com",
            password_hash=hash_password("Admin1234!"),
            name="Admin",
            role="admin",
        )
    )
    s.close()
    return create_app(db_path=db_path)


class TestFirstRunLockdown:
    def test_dashboard_root_redirects_to_setup_when_no_users(self, app_empty):
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/")
            assert r.status_code == 302
            assert r.headers["location"].startswith("/landing")
            assert "setup=1" in r.headers["location"]

    def test_resources_page_redirects_to_setup_when_no_users(self, app_empty):
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/resources")
            assert r.status_code == 302
            assert "/landing" in r.headers["location"]

    def test_landing_still_reachable_when_no_users(self, app_empty):
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/landing")
            assert r.status_code == 200

    def test_login_page_still_reachable_when_no_users(self, app_empty):
        # /login itself loads — but no credentials work because no user exists.
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/login")
            assert r.status_code == 200

    def test_login_post_with_no_users_does_not_authenticate(self, app_empty):
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.post(
                "/login",
                data={
                    "email": "anyone@anywhere.com",
                    "password": "Anything123!",
                    "csrf_token": "",
                },
                follow_redirects=False,
            )
            # Either back to login with an error, or a non-200; the key
            # is that no session cookie should be set.
            assert "session_id" not in r.cookies

    def test_static_assets_reachable_when_no_users(self, app_empty):
        # The setup landing page needs CSS — /static must remain public.
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/static/style.css")
            assert r.status_code == 200

    def test_api_health_reachable_when_no_users(self, app_empty):
        # Health check exists for uptime monitors; cannot be hidden.
        with TestClient(app_empty, follow_redirects=False) as client:
            r = client.get("/api/v1/health")
            assert r.status_code == 200

    def test_dashboard_works_normally_once_admin_exists(self, app_seeded):
        # Sanity: with an admin user, the lockdown is OFF — root redirects
        # to /landing (the marketing root) for an anonymous visitor, NOT
        # to ?setup=1.
        with TestClient(app_seeded, follow_redirects=False) as client:
            r = client.get("/")
            # Not authenticated → 302 to /landing (no setup=1) per the
            # existing post-login redirect logic.
            assert r.status_code == 302
            loc = r.headers["location"]
            assert "setup=1" not in loc
