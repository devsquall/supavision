"""Dashboard trigger handlers go through the shared `run_triggers` helper.

Both `/resources/{id}/discover` and `/resources/{id}/health-check` must:
- Return **202** with `{"ok": true, "run_id": "..."}` on success.
- Return **409** with `{"error": "run_in_flight", "active_run_id": "..."}` when
  a PENDING/RUNNING run already exists for the resource.
- Create exactly one Run row (no orphan PENDING).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Run, RunStatus, RunType, User
from supavision.web.auth import hash_password
from supavision.web.dashboard import router as dashboard_router


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test_dash_triggers.db")
    yield s
    s.close()


def _make_app(store: Store) -> FastAPI:
    app = FastAPI()
    admin = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin",
    )
    store.create_user(admin)

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.csrf_token = "test-csrf"
        request.state.current_user = admin
        request.state.is_admin = True
        return await call_next(request)

    static_dir = Path(__file__).resolve().parent.parent / "src" / "supavision" / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.store = store

    # Engine mock: trigger_run_or_409 schedules the bg task; we want it to
    # transition the Run to COMPLETED quickly so the test can assert on the
    # final state without waiting.
    async def _complete_run(rid, run_id=None):
        if run_id:
            run = store.get_run(run_id)
            if run:
                run.status = RunStatus.COMPLETED
                store.save_run(run)

    mock_engine = MagicMock()
    mock_engine.run_discovery_async = _complete_run
    mock_engine.run_health_check_async = _complete_run
    app.state.engine = mock_engine
    app.state.scheduler = MagicMock()
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def client(store):
    return TestClient(_make_app(store), raise_server_exceptions=False)


@pytest.fixture
def resource(store):
    r = Resource(name="srv", resource_type="server", config={"ssh_host": "1.2.3.4"})
    store.save_resource(r)
    return r


class TestDashboardTriggers:
    def test_discover_returns_202_with_run_id(self, client, store, resource):
        r = client.post(
            f"/resources/{resource.id}/discover",
            headers={"x-csrf-token": "test-csrf"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body.get("ok") is True
        assert "run_id" in body
        # Exactly one row for this resource — no duplicate.
        runs = store.get_runs(resource.id, limit=100)
        assert len(runs) == 1
        assert runs[0].id == body["run_id"]

    def test_health_check_returns_202_with_run_id(self, client, store, resource):
        r = client.post(
            f"/resources/{resource.id}/health-check",
            headers={"x-csrf-token": "test-csrf"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body.get("ok") is True
        assert "run_id" in body

    def test_discover_returns_409_when_run_in_flight(self, client, store, resource):
        existing = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.PENDING,
        )
        store.save_run(existing)

        r = client.post(
            f"/resources/{resource.id}/discover",
            headers={"x-csrf-token": "test-csrf"},
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "run_in_flight"
        assert detail["active_run_id"] == existing.id

    def test_health_check_returns_409_when_other_run_active(self, client, store, resource):
        # Any active run on the resource (even a different run_type) blocks.
        existing = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.RUNNING,
        )
        store.save_run(existing)

        r = client.post(
            f"/resources/{resource.id}/health-check",
            headers={"x-csrf-token": "test-csrf"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["active_run_id"] == existing.id

    def test_discover_missing_resource_returns_404(self, client):
        r = client.post(
            "/resources/does-not-exist/discover",
            headers={"x-csrf-token": "test-csrf"},
        )
        assert r.status_code == 404
