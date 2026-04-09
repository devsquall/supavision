"""Tests for the FastAPI REST API — auth, resources CRUD, health."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, RunStatus, RunType
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
