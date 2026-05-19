"""Verify /docs, /redoc, /openapi.json are admin-only.

Before this fix, any authenticated session user — viewer included — could
load /docs and learn the full mutating-API surface. Viewers couldn't *call*
admin-only endpoints (those still require an admin API key), but they could
discover endpoint names, query/body shapes, and any internal-only fields
exposed by Pydantic models.

The fix: FastAPI's built-in /docs et al. are disabled, and we mount our own
routes that 403 unless `request.state.is_admin` is True.

These tests bypass /login (the login rate limit makes per-test logins flaky)
by creating a session row directly in the DB and setting the cookie.
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
    return str(tmp_path / "test_docs.db")


def _seed_user_and_session(db_path: str, email: str, role: str) -> str:
    """Create a user + an active session, return the session_id cookie value."""
    s = Store(db_path)
    user = User(
        email=email,
        password_hash=hash_password("Pass1234!"),
        name=email.split("@")[0],
        role=role,
    )
    s.create_user(user)
    sess = Session(user_id=user.id)
    s.create_session(sess)
    s.close()
    return sess.id


@pytest.fixture
def app_factory(db_path):
    """Yield a factory so each test can seed the DB before app creation."""

    def _make():
        return create_app(db_path=db_path)

    return _make


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_unauthenticated_request_redirects_to_login(db_path, app_factory, path):
    # Need at least one user so we don't trip first-run lockdown.
    _seed_user_and_session(db_path, "admin@test.com", "admin")
    app = app_factory()
    with TestClient(app, follow_redirects=False) as c:
        r = c.get(path)
        # Session middleware sends unauth users to /login with a next= param.
        assert r.status_code == 302
        assert "/login" in r.headers["location"]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_viewer_gets_403(db_path, app_factory, path):
    session_id = _seed_user_and_session(db_path, "viewer@test.com", "viewer")
    app = app_factory()
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get(path)
        assert r.status_code == 403, f"viewer should not see {path}, got {r.status_code}"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_admin_gets_200(db_path, app_factory, path):
    session_id = _seed_user_and_session(db_path, "admin@test.com", "admin")
    app = app_factory()
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get(path)
        assert r.status_code == 200, f"admin should see {path}, got {r.status_code}"


def test_admin_openapi_has_actual_api_paths(db_path, app_factory):
    """Smoke-check the OpenAPI doc actually describes the app."""
    session_id = _seed_user_and_session(db_path, "admin@test.com", "admin")
    app = app_factory()
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/openapi.json")
        assert r.status_code == 200
        data = r.json()
        assert "paths" in data
        assert any(p.startswith("/api/v1/") for p in data["paths"]), data["paths"]
