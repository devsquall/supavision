"""Functional tests for dashboard HTML routes.

Tests core GET pages, RBAC enforcement on POST routes, and error cases.
Uses a minimal FastAPI app with fake auth middleware instead of the full app factory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, User
from supavision.web.auth import hash_password
from supavision.web.dashboard import router as dashboard_router

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_dash.db"
    s = Store(db_path)
    yield s
    s.close()


def _make_app(store: Store, *, is_admin: bool = True) -> FastAPI:
    """Build a minimal FastAPI app with the dashboard router and fake auth."""
    app = FastAPI()

    admin_user = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin" if is_admin else "viewer",
    )
    store.create_user(admin_user)

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.csrf_token = "test-csrf"
        request.state.current_user = admin_user
        request.state.is_admin = is_admin
        return await call_next(request)

    static_dir = Path(__file__).resolve().parent.parent / "src" / "supavision" / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.state.store = store
    app.state.engine = None
    app.state.scheduler = None
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def app(store):
    return _make_app(store, is_admin=True)


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def viewer_app(tmp_path):
    """App with viewer-level (non-admin) auth and its own store."""
    db_path = tmp_path / "test_viewer.db"
    s = Store(db_path)
    app = _make_app(s, is_admin=False)
    yield app
    s.close()


@pytest.fixture
def viewer_client(viewer_app):
    return TestClient(viewer_app, raise_server_exceptions=False)


def _seed_resource(store: Store, name: str = "test-server", resource_type: str = "server") -> Resource:
    """Create and persist a test resource."""
    r = Resource(name=name, resource_type=resource_type)
    store.save_resource(r)
    return r




# ── Priority 1: Auth routes ─────────────────────────────────────


class TestLoginPage:
    def test_login_get_redirects_when_authenticated(self, client):
        """Authenticated user hitting /login should redirect."""
        resp = client.get("/login", follow_redirects=False)
        # Our fake middleware always sets current_user, so we should get a redirect
        assert resp.status_code == 307 or resp.status_code == 302

    def test_logout_clears_session(self, client):
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("location", "")


# ── Priority 2: Core read routes (admin client) ─────────────────


class TestDashboardHome:
    def test_home_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_dashboard_overview(self, client):
        resp = client.get("/dashboard/overview")
        assert resp.status_code == 200
        assert "Awaiting Data" in resp.text or "Operational" in resp.text

    def test_dashboard_live_activity(self, client):
        resp = client.get("/dashboard/live-activity")
        assert resp.status_code == 200


class TestResourcesPage:
    def test_resources_list(self, client):
        resp = client.get("/resources")
        assert resp.status_code == 200

    def test_resources_list_with_data(self, client, store):
        _seed_resource(store)
        resp = client.get("/resources")
        assert resp.status_code == 200
        assert "test-server" in resp.text

    def test_resource_new_page(self, client):
        resp = client.get("/resources/new")
        assert resp.status_code == 200

    def test_resource_detail(self, client, store):
        r = _seed_resource(store)
        resp = client.get(f"/resources/{r.id}")
        assert resp.status_code == 200
        assert "test-server" in resp.text

    def test_resource_history(self, client, store):
        r = _seed_resource(store)
        resp = client.get(f"/resources/{r.id}/history")
        assert resp.status_code == 200

    def test_resource_edit_page(self, client, store):
        r = _seed_resource(store)
        resp = client.get(f"/resources/{r.id}/edit")
        assert resp.status_code == 200


class TestSettingsPage:
    def test_settings_page(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_settings_users_page(self, client):
        resp = client.get("/settings/users")
        assert resp.status_code == 200


class TestSchedulesPage:
    @patch("supavision.scheduler.get_scheduler_status")
    def test_schedules_page(self, mock_status, client):
        mock_status.return_value = {"running": False, "healthy": False, "last_tick_at": None}
        resp = client.get("/schedules")
        assert resp.status_code == 200


class TestActivityPage:
    def test_activity_page(self, client):
        resp = client.get("/activity")
        assert resp.status_code == 200

    def test_activity_page_with_range(self, client):
        resp = client.get("/activity?range=7d")
        assert resp.status_code == 200

    def test_activity_live(self, client):
        resp = client.get("/activity/live")
        assert resp.status_code == 200

    def test_activity_active_json(self, client):
        resp = client.get("/api/activity/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


class TestAskPage:
    def test_ask_page(self, client):
        resp = client.get("/ask")
        assert resp.status_code == 200


class TestCommandCenterPage:
    def test_command_center_page(self, client):
        resp = client.get("/command-center")
        assert resp.status_code == 200


class TestAlertsPage:
    def test_alerts_page(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == 200


class TestReportsPage:
    def test_reports_page(self, client):
        resp = client.get("/reports")
        assert resp.status_code == 200


class TestSessionsPage:
    def test_sessions_page(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200

    def test_sessions_tab_jobs(self, client):
        resp = client.get("/sessions?tab=jobs")
        assert resp.status_code == 200


class TestMetricsPage:
    def test_metrics_page(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200


class TestProfilePage:
    def test_profile_page(self, client):
        resp = client.get("/profile")
        assert resp.status_code == 200


# ── Priority 3: RBAC enforcement on POST routes ─────────────────


class TestRBACResources:
    def test_viewer_cannot_delete_resource(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/delete")
        assert resp.status_code == 403

    def test_viewer_cannot_discover(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/discover")
        assert resp.status_code == 403

    def test_viewer_cannot_health_check(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/health-check")
        assert resp.status_code == 403

    def test_viewer_cannot_toggle_resource(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/toggle")
        assert resp.status_code == 403

    def test_viewer_cannot_edit_resource(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/edit", data={"name": "hacked"})
        assert resp.status_code == 403

    def test_viewer_cannot_create_resource(self, viewer_client):
        resp = viewer_client.post(
            "/resources/new",
            data={"name": "evil", "resource_type": "server"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_schedule(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/resources/{r.id}/schedule", data={"health_cron": "* * * * *"})
        assert resp.status_code == 403


class TestRBACSettings:
    def test_viewer_cannot_create_api_key(self, viewer_client):
        resp = viewer_client.post("/settings/api-keys", data={"label": "evil"})
        assert resp.status_code == 403

    def test_viewer_cannot_revoke_api_key(self, viewer_client):
        resp = viewer_client.post("/settings/api-keys/fake-id/revoke")
        assert resp.status_code == 403

    def test_viewer_cannot_check_claude(self, viewer_client):
        resp = viewer_client.post("/settings/check-claude")
        assert resp.status_code == 403


class TestRBACSchedules:
    def test_viewer_cannot_toggle_schedule(self, viewer_client, viewer_app):
        store = viewer_app.state.store
        r = _seed_resource(store)
        resp = viewer_client.post(f"/schedules/{r.id}/toggle")
        assert resp.status_code == 403


class TestRBACCommandCenter:
    def test_viewer_cannot_query(self, viewer_client):
        resp = viewer_client.post(
            "/command-center/query",
            data={"command": "system_overview"},
        )
        assert resp.status_code == 403


class TestRBACUserManagement:
    def test_viewer_cannot_view_users_page(self, viewer_client):
        resp = viewer_client.get("/settings/users")
        assert resp.status_code == 403

    def test_viewer_cannot_create_user(self, viewer_client):
        resp = viewer_client.post(
            "/settings/users/create",
            data={"email": "evil@test.com", "password": "Evil1234!", "name": "Evil"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_toggle_user(self, viewer_client):
        resp = viewer_client.post("/settings/users/fake-id/toggle")
        assert resp.status_code == 403

    def test_viewer_cannot_change_role(self, viewer_client):
        resp = viewer_client.post("/settings/users/fake-id/role", data={"role": "admin"})
        assert resp.status_code == 403


# ── Priority 4: Error cases ─────────────────────────────────────


class TestNotFound:
    def test_resource_not_found(self, client):
        resp = client.get("/resources/nonexistent-id")
        assert resp.status_code == 404

    def test_resource_history_not_found(self, client):
        resp = client.get("/resources/nonexistent-id/history")
        assert resp.status_code == 404

    def test_resource_edit_not_found(self, client):
        resp = client.get("/resources/nonexistent-id/edit")
        assert resp.status_code == 404

    def test_session_run_not_found(self, client):
        resp = client.get("/sessions/run/nonexistent-id")
        assert resp.status_code == 404

    def test_session_job_not_found(self, client):
        resp = client.get("/sessions/job/nonexistent-id")
        assert resp.status_code == 404

    def test_session_invalid_type(self, client):
        resp = client.get("/sessions/invalid/some-id")
        assert resp.status_code == 404


# ── Admin POST routes (happy paths) ─────────────────────────────


class TestAdminActions:
    def test_create_api_key(self, client):
        resp = client.post("/settings/api-keys", data={"label": "test-key"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/settings" in resp.headers.get("location", "")

    def test_create_api_key_no_label(self, client):
        resp = client.post("/settings/api-keys", data={"label": ""}, follow_redirects=False)
        assert resp.status_code in (302, 303)

    @patch("supavision.scheduler.get_scheduler_status")
    def test_toggle_schedule(self, mock_status, client, store):
        mock_status.return_value = {"running": False, "healthy": False, "last_tick_at": None}
        r = _seed_resource(store)
        resp = client.post(f"/schedules/{r.id}/toggle")
        assert resp.status_code == 204

    def test_delete_resource(self, client, store):
        r = _seed_resource(store)
        resp = client.post(f"/resources/{r.id}/delete", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_command_center_system_overview(self, client):
        resp = client.post(
            "/command-center/query",
            data={"command": "system_overview", "resource_id": "", "severity": ""},
        )
        assert resp.status_code == 200
        assert "System Overview" in resp.text or "Awaiting Data" in resp.text

    def test_command_center_project_stats(self, client):
        resp = client.post(
            "/command-center/query",
            data={"command": "project_stats", "resource_id": "", "severity": ""},
        )
        assert resp.status_code == 200

    def test_command_center_unknown_command(self, client):
        resp = client.post(
            "/command-center/query",
            data={"command": "does_not_exist", "resource_id": "", "severity": ""},
        )
        assert resp.status_code == 200
        assert "Unknown" in resp.text


# ── Form Input Validation ─────────────────────────────────────────


class TestAdminFormValidation:
    def test_new_resource_name_too_long_returns_400(self, client):
        resp = client.post("/resources/new", data={"name": "x" * 201, "resource_type": "server"})
        assert resp.status_code == 400

    def test_edit_name_too_long_returns_400(self, client, store):
        r = _seed_resource(store)
        resp = client.post(f"/resources/{r.id}/edit", data={"name": "x" * 201})
        assert resp.status_code == 400

    def test_checklist_item_too_long_returns_400(self, client, store):
        r = _seed_resource(store)
        resp = client.post(f"/resources/{r.id}/checklist", data={"request": "x" * 501})
        assert resp.status_code == 400

    def test_checklist_too_many_items_returns_400(self, client, store):
        r = _seed_resource(store)
        r.monitoring_requests = [f"item {i}" for i in range(50)]
        store.save_resource(r)
        resp = client.post(f"/resources/{r.id}/checklist", data={"request": "one more"})
        assert resp.status_code == 400
