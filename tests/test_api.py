"""Tests for the FastAPI REST API — auth, resources CRUD, health."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Run, RunStatus, RunType
from supavision.web.auth import generate_api_key, hash_api_key
from supavision.web.routes import health_router, router

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temp database."""
    db_path = tmp_path / "test_api.db"
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def app(store):
    """Create a FastAPI app wired to the test store."""
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(router)
    app.state.store = store
    # Provide a mock engine so route handlers don't fail
    app.state.engine = MagicMock()
    app.state.scheduler = MagicMock()
    return app


@pytest.fixture
def api_key(store) -> str:
    """Create a valid API key in the store and return the raw key."""
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="test")
    return raw_key


@pytest.fixture
def client(app, api_key) -> TestClient:
    """TestClient with valid auth header."""
    return TestClient(app, headers={"x-api-key": api_key})


@pytest.fixture
def unauth_client(app) -> TestClient:
    """TestClient without any auth header."""
    return TestClient(app)


# ── Auth tests ───────────────────────────────────────────────────


class TestAuth:
    def test_health_no_auth_required(self, unauth_client):
        resp = unauth_client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_missing_api_key_returns_401(self, unauth_client):
        resp = unauth_client.get("/api/v1/resources")
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"]

    def test_invalid_api_key_returns_401(self, app):
        client = TestClient(app, headers={"x-api-key": "invalid-key-12345"})
        resp = client.get("/api/v1/resources")
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    def test_valid_api_key_returns_200(self, client):
        resp = client.get("/api/v1/resources")
        assert resp.status_code == 200

    def test_revoked_key_returns_401(self, app, store):
        key_id, raw_key, key_hash = generate_api_key()
        store.save_api_key(key_id, key_hash, label="to-revoke")
        store.revoke_api_key(key_id)

        client = TestClient(app, headers={"x-api-key": raw_key})
        resp = client.get("/api/v1/resources")
        assert resp.status_code == 401
        assert "Invalid or revoked" in resp.json()["detail"]

    def test_empty_api_key_header_returns_401(self, app):
        client = TestClient(app, headers={"x-api-key": ""})
        resp = client.get("/api/v1/resources")
        assert resp.status_code == 401


# ── Health endpoint ──────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "supavision"


# ── Resource CRUD ────────────────────────────────────────────────


class TestResources:
    def test_list_resources_empty(self, client):
        resp = client.get("/api/v1/resources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["resources"] == []

    def test_create_resource(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={
                "name": "prod-server",
                "resource_type": "server",
                "config": {"region": "us-east-1"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "prod-server"
        assert "resource_id" in data

    def test_create_and_list_resource(self, client):
        client.post(
            "/api/v1/resources",
            json={"name": "server-1", "resource_type": "server"},
        )
        client.post(
            "/api/v1/resources",
            json={"name": "server-2", "resource_type": "server"},
        )

        resp = client.get("/api/v1/resources")
        data = resp.json()
        assert len(data["resources"]) == 2
        names = {r["name"] for r in data["resources"]}
        assert names == {"server-1", "server-2"}

    def test_get_resource_detail(self, client, store):
        resource = Resource(name="detail-test", resource_type="server")
        store.save_resource(resource)

        resp = client.get(f"/api/v1/resources/{resource.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["resource"]["name"] == "detail-test"
        assert data["context"] is None  # No context yet
        assert data["checklist"] is None
        assert data["recent_runs"] == []

    def test_get_nonexistent_resource_returns_404(self, client):
        resp = client.get("/api/v1/resources/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_resource(self, client, store):
        resource = Resource(name="to-delete", resource_type="server")
        store.save_resource(resource)

        resp = client.delete(f"/api/v1/resources/{resource.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["deleted"] == resource.id

        # Verify it's gone
        resp = client.get(f"/api/v1/resources/{resource.id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/v1/resources/nonexistent-id")
        assert resp.status_code == 404

    def test_create_resource_with_parent(self, client, store):
        parent = Resource(name="parent", resource_type="server")
        store.save_resource(parent)

        resp = client.post(
            "/api/v1/resources",
            json={
                "name": "child",
                "resource_type": "server",
                "parent_id": parent.id,
            },
        )
        assert resp.status_code == 200

    def test_create_resource_missing_name_returns_422(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"resource_type": "server"},
        )
        assert resp.status_code == 422

    def test_create_resource_missing_type_returns_422(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"name": "test"},
        )
        assert resp.status_code == 422

    def test_resource_list_includes_metadata(self, client, store):
        resource = Resource(name="meta-test", resource_type="server")
        store.save_resource(resource)

        resp = client.get("/api/v1/resources")
        data = resp.json()
        r = data["resources"][0]
        assert r["id"] == resource.id
        assert r["name"] == "meta-test"
        assert r["resource_type"] == "server"
        assert r["created_at"] is not None
        assert r["latest_severity"] is None
        assert r["latest_run_status"] is None


# ── Runs and Reports ─────────────────────────────────────────────


class TestRunsAndReports:
    def test_get_nonexistent_run_returns_404(self, client):
        resp = client.get("/api/v1/runs/nonexistent-id")
        assert resp.status_code == 404

    def test_get_run_returns_data(self, client, store):
        from supavision.models import Run

        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)

        run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
        )
        store.save_run(run)

        resp = client.get(f"/api/v1/runs/{run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["run"]["status"] == "completed"

    def test_list_reports_requires_resource_id(self, client):
        resp = client.get("/api/v1/reports")
        assert resp.status_code == 400

    def test_list_reports_for_resource(self, client, store):
        from supavision.models import Report

        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)

        report = Report(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            content="Test report",
        )
        store.save_report(report)

        resp = client.get(
            "/api/v1/reports",
            params={"resource_id": resource.id, "run_type": "health_check"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["reports"]) == 1

    def test_get_run_with_report_and_evaluation(self, client, store):
        """Run endpoint attaches report and evaluation when present."""
        from supavision.models import Evaluation, Report, Run, Severity

        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)

        report = Report(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            content="Health check passed",
        )
        store.save_report(report)

        evaluation = Evaluation(
            report_id=report.id,
            resource_id=resource.id,
            severity=Severity.HEALTHY,
            summary="All good",
        )
        store.save_evaluation(evaluation)

        run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            report_id=report.id,
            evaluation_id=evaluation.id,
        )
        store.save_run(run)

        resp = client.get(f"/api/v1/runs/{run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data["run"]
        assert data["run"]["report"]["content"] == "Health check passed"
        assert "evaluation" in data["run"]
        assert data["run"]["evaluation"]["severity"] == "healthy"

    def test_resource_list_with_latest_severity(self, client, store):
        """List resources shows latest severity when evaluations exist."""
        from supavision.models import Evaluation, Run, Severity

        resource = Resource(name="sev-test", resource_type="server")
        store.save_resource(resource)

        run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
        )
        store.save_run(run)

        evaluation = Evaluation(
            report_id="rpt-1",
            resource_id=resource.id,
            severity=Severity.WARNING,
            summary="Disk high",
        )
        store.save_evaluation(evaluation)

        resp = client.get("/api/v1/resources")
        data = resp.json()
        r = next(item for item in data["resources"] if item["id"] == resource.id)
        assert r["latest_run_status"] == "completed"
        assert r["latest_severity"] == "warning"


# ── Trigger endpoints ────────────────────────────────────────────


class TestTriggers:
    def test_trigger_discovery_returns_run_id(self, client, store):
        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)

        resp = client.post(f"/api/v1/resources/{resource.id}/discover")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "run_id" in data

    def test_trigger_discovery_nonexistent_resource(self, client):
        resp = client.post("/api/v1/resources/nonexistent/discover")
        assert resp.status_code == 404

    def test_trigger_health_check_returns_run_id(self, client, store):
        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)

        resp = client.post(f"/api/v1/resources/{resource.id}/health-check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "run_id" in data

    def test_trigger_health_check_nonexistent_resource(self, client):
        resp = client.post("/api/v1/resources/nonexistent/health-check")
        assert resp.status_code == 404


# ── Notify test endpoint ─────────────────────────────────────────


class TestNotifyTest:
    def test_notify_test_nonexistent_resource(self, client):
        resp = client.post("/api/v1/resources/nonexistent/notify-test")
        assert resp.status_code == 404

    def test_notify_test_no_webhooks_configured(self, client, store):
        resource = Resource(name="test", resource_type="server", config={})
        store.save_resource(resource)

        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("SLACK_WEBHOOK", None)
            resp = client.post(f"/api/v1/resources/{resource.id}/notify-test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["channels"] == []

    def test_notify_test_with_slack(self, client, store):
        resource = Resource(
            name="test",
            resource_type="server",
            config={"slack_webhook": "https://hooks.slack.com/test"},
        )
        store.save_resource(resource)

        with patch(
            "supavision.notifications.SlackChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = client.post(f"/api/v1/resources/{resource.id}/notify-test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "slack" in data["channels"]


# ── Auth helper functions ────────────────────────────────────────


class TestAuthHelpers:
    def test_generate_api_key_format(self):
        key_id, raw_key, key_hash = generate_api_key()
        assert raw_key.startswith("sv_")
        assert len(raw_key) > 20
        assert key_hash == hashlib.sha256(raw_key.encode()).hexdigest()

    def test_hash_api_key_deterministic(self):
        h1 = hash_api_key("test-key")
        h2 = hash_api_key("test-key")
        assert h1 == h2

    def test_hash_api_key_different_for_different_keys(self):
        h1 = hash_api_key("key-1")
        h2 = hash_api_key("key-2")
        assert h1 != h2


# ── Helper fixtures ─────────────────────────────


@pytest.fixture
def viewer_api_key(store) -> str:
    """Create a viewer-role API key and return the raw key."""
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="viewer", role="viewer")
    return raw_key


@pytest.fixture
def viewer_client(app, viewer_api_key) -> TestClient:
    """TestClient with viewer-role auth header."""
    return TestClient(app, headers={"x-api-key": viewer_api_key})


class TestMetrics:
    def test_get_metrics_empty(self, client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = client.get(f"/api/v1/resources/{resource.id}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["metrics"] == {}

    def test_get_metrics_with_data(self, client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        store.save_metrics(resource.id, "report-1", [
            {"name": "cpu", "value": 45.0, "unit": "%"},
            {"name": "memory", "value": 72.5, "unit": "%"},
        ])

        resp = client.get(f"/api/v1/resources/{resource.id}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["metrics"]["cpu"] == 45.0
        assert data["metrics"]["memory"] == 72.5

    def test_get_metrics_not_found(self, client):
        resp = client.get("/api/v1/resources/nonexistent/metrics")
        assert resp.status_code == 404

    def test_get_metric_trend(self, client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        store.save_metrics(resource.id, "report-1", [
            {"name": "cpu", "value": 45.0, "unit": "%"},
        ])
        store.save_metrics(resource.id, "report-2", [
            {"name": "cpu", "value": 50.0, "unit": "%"},
        ])

        resp = client.get(f"/api/v1/resources/{resource.id}/metrics/cpu")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["metric"] == "cpu"
        assert len(data["data"]) == 2

    def test_get_metric_trend_not_found(self, client):
        resp = client.get("/api/v1/resources/nonexistent/metrics/cpu")
        assert resp.status_code == 404


# ── Incidents endpoint ───────────────────────────────────────────


class TestIncidents:
    def test_get_incidents_empty(self, client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = client.get(f"/api/v1/resources/{resource.id}/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["incidents"] == []

    def test_get_incidents_with_transitions(self, client, store):
        from supavision.models import Evaluation, Severity

        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        # Create evaluations with different severities to produce a transition
        ev1 = Evaluation(
            report_id="rpt-1",
            resource_id=resource.id,
            severity=Severity.HEALTHY,
            summary="All good",
        )
        store.save_evaluation(ev1)

        ev2 = Evaluation(
            report_id="rpt-2",
            resource_id=resource.id,
            severity=Severity.WARNING,
            summary="Disk usage high",
        )
        store.save_evaluation(ev2)

        resp = client.get(f"/api/v1/resources/{resource.id}/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["incidents"]) == 1
        incident = data["incidents"][0]
        assert incident["from_severity"] == "healthy"
        assert incident["to_severity"] == "warning"

    def test_get_incidents_not_found(self, client):
        resp = client.get("/api/v1/resources/nonexistent/incidents")
        assert resp.status_code == 404
class TestSystemStatus:
    def test_system_status(self, client):
        with patch("supavision.scheduler.get_scheduler_status") as mock_sched:
            mock_sched.return_value = {"running": True, "jobs": 3}
            resp = client.get("/api/v1/system/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "version" in data
        assert data["scheduler"] == {"running": True, "jobs": 3}


# ── RBAC — Viewer role tests ─────────────────────────────────────


class TestRBACViewer:
    """Viewer API keys should have read access but not write access."""

    # ── GET endpoints: viewer should get 200 ──

    def test_viewer_can_list_resources(self, viewer_client):
        resp = viewer_client.get("/api/v1/resources")
        assert resp.status_code == 200

    def test_viewer_can_get_resource_detail(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.get(f"/api/v1/resources/{resource.id}")
        assert resp.status_code == 200

    def test_viewer_can_get_metrics(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.get(f"/api/v1/resources/{resource.id}/metrics")
        assert resp.status_code == 200

    def test_viewer_can_get_incidents(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.get(f"/api/v1/resources/{resource.id}/incidents")
        assert resp.status_code == 200

    def test_viewer_can_get_system_status(self, viewer_client):
        with patch("supavision.scheduler.get_scheduler_status", return_value={}):
            resp = viewer_client.get("/api/v1/system/status")
        assert resp.status_code == 200

    # ── POST/DELETE endpoints: viewer should get 403 ──

    def test_viewer_cannot_create_resource(self, viewer_client):
        resp = viewer_client.post(
            "/api/v1/resources",
            json={"name": "test", "resource_type": "server"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_delete_resource(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.delete(f"/api/v1/resources/{resource.id}")
        assert resp.status_code == 403

    def test_viewer_cannot_trigger_discovery(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.post(f"/api/v1/resources/{resource.id}/discover")
        assert resp.status_code == 403

    def test_viewer_cannot_trigger_health_check(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.post(f"/api/v1/resources/{resource.id}/health-check")
        assert resp.status_code == 403

    def test_viewer_cannot_send_notify_test(self, viewer_client, store):
        resource = Resource(name="server", resource_type="server")
        store.save_resource(resource)

        resp = viewer_client.post(f"/api/v1/resources/{resource.id}/notify-test")
        assert resp.status_code == 403


class TestUpdateResource:
    def test_update_name(self, client, store):
        resource = Resource(name="original", resource_type="server")
        store.save_resource(resource)
        resp = client.put(f"/api/v1/resources/{resource.id}", json={"name": "updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated"

    def test_update_config(self, client, store):
        resource = Resource(name="test", resource_type="server", config={"region": "us-east"})
        store.save_resource(resource)
        resp = client.put(f"/api/v1/resources/{resource.id}", json={"config": {"zone": "az1"}})
        assert resp.status_code == 200
        # Verify config was merged
        updated = store.get_resource(resource.id)
        assert updated.config["region"] == "us-east"
        assert updated.config["zone"] == "az1"

    def test_update_not_found(self, client):
        resp = client.put("/api/v1/resources/nonexistent", json={"name": "x"})
        assert resp.status_code == 404

    def test_update_requires_admin(self, app, store):
        # Create viewer key
        from supavision.web.auth import generate_api_key

        key_id, raw_key, key_hash = generate_api_key()
        store.save_api_key(key_id, key_hash, label="viewer", role="viewer")
        viewer = TestClient(app, headers={"x-api-key": raw_key})

        resource = Resource(name="test", resource_type="server")
        store.save_resource(resource)
        resp = viewer.put(f"/api/v1/resources/{resource.id}", json={"name": "hacked"})
        assert resp.status_code == 403


class TestTriggerRun:
    """POST /api/v1/runs — unified run trigger endpoint."""

    def test_trigger_discovery_returns_run_id(self, client, store):
        resource = Resource(name="srv", resource_type="server")
        store.save_resource(resource)
        resp = client.post("/api/v1/runs", json={"resource_id": resource.id, "run_type": "discovery"})
        assert resp.status_code == 200
        assert "run_id" in resp.json()

    def test_trigger_health_check_returns_run_id(self, client, store):
        resource = Resource(name="srv", resource_type="server")
        store.save_resource(resource)
        resp = client.post("/api/v1/runs", json={"resource_id": resource.id, "run_type": "health_check"})
        assert resp.status_code == 200
        assert "run_id" in resp.json()

    def test_trigger_creates_run_with_correct_type(self, client, store):
        resource = Resource(name="srv", resource_type="server")
        store.save_resource(resource)
        resp = client.post("/api/v1/runs", json={"resource_id": resource.id, "run_type": "discovery"})
        run_id = resp.json()["run_id"]
        run = store.get_run(run_id)
        assert run is not None
        assert str(run.run_type) == "discovery"

    def test_trigger_invalid_run_type_returns_400(self, client, store):
        resource = Resource(name="srv", resource_type="server")
        store.save_resource(resource)
        resp = client.post("/api/v1/runs", json={"resource_id": resource.id, "run_type": "invalid"})
        assert resp.status_code == 400

    def test_trigger_unknown_resource_returns_404(self, client):
        resp = client.post("/api/v1/runs", json={"resource_id": "does-not-exist", "run_type": "discovery"})
        assert resp.status_code == 404

    def test_trigger_run_in_progress_returns_409(self, client, store):
        resource = Resource(name="srv", resource_type="server")
        store.save_resource(resource)
        # Create an in-progress run
        run = Run(resource_id=resource.id, run_type=RunType.HEALTH_CHECK, status=RunStatus.RUNNING)
        store.save_run(run)
        resp = client.post("/api/v1/runs", json={"resource_id": resource.id, "run_type": "health_check"})
        assert resp.status_code == 409


# ── Input Validation ─────────────────────────────────────────────


class TestResourceValidation:
    def test_name_too_long_returns_422(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"name": "x" * 201, "resource_type": "server"},
        )
        assert resp.status_code == 422

    def test_name_at_limit_passes(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"name": "x" * 200, "resource_type": "server"},
        )
        assert resp.status_code == 200

    def test_config_value_too_long_returns_422(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"name": "ok", "resource_type": "server", "config": {"ssh_host": "h" * 501}},
        )
        assert resp.status_code == 422

    def test_config_too_many_entries_returns_422(self, client):
        resp = client.post(
            "/api/v1/resources",
            json={"name": "ok", "resource_type": "server", "config": {f"k{i}": "v" for i in range(51)}},
        )
        assert resp.status_code == 422

    def test_update_name_too_long_returns_422(self, client, store):
        create = client.post(
            "/api/v1/resources",
            json={"name": "ok", "resource_type": "server"},
        )
        rid = create.json()["resource_id"]
        resp = client.put(f"/api/v1/resources/{rid}", json={"name": "x" * 201})
        assert resp.status_code == 422
