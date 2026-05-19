"""Tests for the command-palette / global-search dual-auth fix.

Three pre-existing bugs in one handler:
1. `request.state.user` was read where middleware sets `request.state.current_user`.
2. `/api/v1/*` is skipped by session middleware, so session users never had
   `current_user` set on this endpoint anyway.
3. `x-api-key` was checked for header-presence only, not validated against the DB.

The fix introduces ``get_auth_context(request)`` which validates either auth
mode itself and returns a typed ``AuthContext`` describing the principal.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Session, User
from supavision.web.auth import (
    AuthContext,
    generate_api_key,
    get_auth_context,
    hash_password,
)
from supavision.web.routes import health_router
from supavision.web.routes import router as api_router

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test_search_auth.db")
    yield s
    s.close()


@pytest.fixture
def app(store):
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(api_router)
    app.state.store = store
    app.state.engine = MagicMock()
    app.state.scheduler = MagicMock()
    return app


@pytest.fixture
def admin_user(store) -> User:
    u = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin",
    )
    store.create_user(u)
    return u


@pytest.fixture
def viewer_user(store) -> User:
    u = User(
        email="viewer@test.com",
        password_hash=hash_password("Viewer123!"),
        name="Viewer",
        role="viewer",
    )
    store.create_user(u)
    return u


@pytest.fixture
def admin_session(store, admin_user) -> Session:
    s = Session(user_id=admin_user.id)
    store.create_session(s)
    return s


def _api_key(store, *, role: str = "admin") -> str:
    key_id, raw, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="test", role=role)
    return raw


# ── Direct tests for get_auth_context ─────────────────────────────


class TestGetAuthContext:
    def test_returns_none_when_no_auth(self, app, store):
        client = TestClient(app)

        # Use a route that calls get_auth_context. We assert via the global
        # search endpoint: no auth → 401, which means get_auth_context returned None.
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 401

    def test_session_branch_validates_cookie_without_middleware(self, app, store, admin_session):
        # /api/v1/* is skipped by the dashboard session middleware (this app doesn't
        # even mount it). The cookie must be validated by the dual-auth helper directly.
        client = TestClient(app, cookies={"session_id": admin_session.id})
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 200

    def test_api_key_branch_validates_against_db(self, app, store):
        client = TestClient(app, headers={"x-api-key": _api_key(store)})
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 200

    def test_invalid_api_key_returns_401_not_200(self, app, store):
        # Closes the "header presence" loophole — a bogus x-api-key must NOT
        # pass auth.
        client = TestClient(app, headers={"x-api-key": "sv_bogus_not_in_db"})
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 401

    def test_invalid_api_key_does_not_fall_back_to_session(self, app, store, admin_session):
        # If a bogus API key is sent, do NOT fall through to session auth —
        # otherwise an attacker can use a stolen cookie even when their API
        # key was revoked.
        client = TestClient(
            app,
            headers={"x-api-key": "sv_bogus"},
            cookies={"session_id": admin_session.id},
        )
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 401

    def test_revoked_api_key_returns_401(self, app, store):
        key_id, raw, key_hash = generate_api_key()
        store.save_api_key(key_id, key_hash, label="test")
        store.revoke_api_key(key_id)
        client = TestClient(app, headers={"x-api-key": raw})
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 401

    def test_invalid_session_id_returns_401(self, app, store):
        client = TestClient(app, cookies={"session_id": "nonexistent"})
        r = client.get("/api/v1/search?q=foo")
        assert r.status_code == 401


# ── Search behavior tests ─────────────────────────────────────────


class TestGlobalSearchResults:
    def test_session_user_can_search_resources(self, app, store, admin_session):
        from supavision.models import Resource

        store.save_resource(Resource(name="prod-web-01", resource_type="server"))
        store.save_resource(Resource(name="ops-db", resource_type="database"))

        client = TestClient(app, cookies={"session_id": admin_session.id})
        r = client.get("/api/v1/search?q=prod")
        assert r.status_code == 200
        names = [hit["name"] for hit in r.json()["results"]]
        assert "prod-web-01" in names

    def test_api_key_user_can_search_resources(self, app, store):
        from supavision.models import Resource

        store.save_resource(Resource(name="api-target", resource_type="server"))

        client = TestClient(app, headers={"x-api-key": _api_key(store)})
        r = client.get("/api/v1/search?q=api")
        assert r.status_code == 200
        names = [hit["name"] for hit in r.json()["results"]]
        assert "api-target" in names

    def test_search_with_short_query_returns_empty(self, app, store, admin_session):
        client = TestClient(app, cookies={"session_id": admin_session.id})
        r = client.get("/api/v1/search?q=a")  # too short, < 2 chars
        assert r.status_code == 200
        assert r.json()["results"] == []


# ── AuthContext typing ────────────────────────────────────────────


class TestAuthContextShape:
    def test_session_context_carries_user_not_api_key_id(self, app, store, admin_session, admin_user):
        # Build a request manually to call get_auth_context directly.
        from starlette.requests import Request

        scope = {
            "type": "http",
            "headers": [(b"cookie", f"session_id={admin_session.id}".encode())],
            "app": app,
        }
        req = Request(scope)
        ctx = get_auth_context(req)
        assert isinstance(ctx, AuthContext)
        assert ctx.source == "session"
        assert ctx.user is not None
        assert ctx.user.id == admin_user.id
        assert ctx.api_key_id is None

    def test_api_key_context_carries_api_key_id_not_user(self, app, store):
        from starlette.requests import Request

        raw = _api_key(store)
        scope = {
            "type": "http",
            "headers": [(b"x-api-key", raw.encode())],
            "app": app,
        }
        req = Request(scope)
        ctx = get_auth_context(req)
        assert isinstance(ctx, AuthContext)
        assert ctx.source == "api_key"
        assert ctx.user is None
        assert ctx.api_key_id is not None


# ── Grep guard ────────────────────────────────────────────────────


class TestNoLingeringRequestStateUserReads:
    """Static guard: nobody should read `request.state.user` again — middleware
    sets `request.state.current_user`, and dual-auth handlers use
    ``get_auth_context``. This regression test catches accidental reintroductions.
    """

    def test_no_request_state_user_reads_in_web_tree(self):
        web_dir = Path(__file__).resolve().parent.parent / "src" / "supavision" / "web"
        # Allow `request.state.current_user` and `request.state.user_agent` —
        # only the bare `request.state.user` pattern is the bug.
        pattern = re.compile(r"request\.state\.user(?![A-Za-z_])")
        offenders: list[tuple[Path, int, str]] = []
        for py in web_dir.rglob("*.py"):
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append((py.relative_to(web_dir), lineno, line.strip()))
        assert not offenders, (
            "Bare `request.state.user` reads are forbidden — the dashboard "
            "session middleware sets `request.state.current_user`. Dual-auth "
            f"handlers must call `get_auth_context(request)`. Offenders: {offenders}"
        )
