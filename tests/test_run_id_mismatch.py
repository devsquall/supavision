"""Tests for the run-ID-mismatch fix.

The engine accepts an optional `run_id` so API trigger endpoints can pre-create
a PENDING row, return its ID synchronously, and have the engine update *that
row* instead of creating a second one. Three API endpoints share this pattern
and an atomic Store helper guards against TOCTOU duplicates.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.engine import Engine
from supavision.models import (
    Resource,
    Run,
    RunMismatchError,
    RunNotFoundError,
    RunStatus,
    RunType,
)
from supavision.web.auth import generate_api_key
from supavision.web.routes import health_router
from supavision.web.routes import router as api_router

# ── Engine helper (_prepare_run) unit tests ───────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test_runid.db")
    yield s
    s.close()


@pytest.fixture
def engine(store, tmp_path):
    # Use a fake template dir — _prepare_run doesn't touch templates.
    return Engine(store=store, template_dir=str(tmp_path / "templates"))


@pytest.fixture
def resource(store) -> Resource:
    r = Resource(name="srv", resource_type="server", config={"ssh_host": "1.2.3.4"})
    store.save_resource(r)
    return r


class TestPrepareRun:
    def test_without_run_id_creates_new_running_run(self, engine, resource, store):
        before = len(store.get_runs(resource.id, limit=100))
        run = engine._prepare_run(resource.id, RunType.DISCOVERY, run_id=None)
        after = len(store.get_runs(resource.id, limit=100))
        assert run.status == RunStatus.RUNNING
        assert run.started_at is not None
        assert after == before + 1

    def test_with_pending_run_id_transitions_in_place(self, engine, resource, store):
        pending = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        before = len(store.get_runs(resource.id, limit=100))
        run = engine._prepare_run(resource.id, RunType.DISCOVERY, run_id=pending.id)
        after = len(store.get_runs(resource.id, limit=100))
        assert run.id == pending.id  # SAME row, not a new one
        assert run.status == RunStatus.RUNNING
        assert after == before  # no new row created

    def test_with_missing_run_id_raises_not_found(self, engine, resource):
        with pytest.raises(RunNotFoundError):
            engine._prepare_run(resource.id, RunType.DISCOVERY, run_id="does-not-exist")

    def test_with_run_id_for_wrong_resource_raises_mismatch(self, engine, store, resource):
        other = Resource(name="other", resource_type="server")
        store.save_resource(other)
        pending = store.create_pending_run_if_no_active(other.id, RunType.DISCOVERY)
        with pytest.raises(RunMismatchError):
            engine._prepare_run(resource.id, RunType.DISCOVERY, run_id=pending.id)

    def test_discovery_run_id_passed_to_health_check_raises_mismatch(self, engine, resource, store):
        pending = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        with pytest.raises(RunMismatchError):
            engine._prepare_run(resource.id, RunType.HEALTH_CHECK, run_id=pending.id)

    def test_running_run_id_raises_mismatch(self, engine, resource, store):
        # A run that's already RUNNING — passing its id is a double-trigger attempt.
        running = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.RUNNING,
        )
        store.save_run(running)
        with pytest.raises(RunMismatchError):
            engine._prepare_run(resource.id, RunType.DISCOVERY, run_id=running.id)

    def test_completed_run_id_raises_mismatch(self, engine, resource, store):
        completed = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.COMPLETED,
        )
        store.save_run(completed)
        with pytest.raises(RunMismatchError):
            engine._prepare_run(resource.id, RunType.DISCOVERY, run_id=completed.id)


# ── Atomic Store helper ───────────────────────────────────────────


class TestCreatePendingRunIfNoActive:
    def test_creates_when_none_active(self, store, resource):
        run = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        assert run is not None
        assert run.status == RunStatus.PENDING

    def test_returns_none_when_pending_exists(self, store, resource):
        store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        second = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        assert second is None

    def test_returns_none_when_running_exists(self, store, resource):
        r = Run(resource_id=resource.id, run_type=RunType.DISCOVERY, status=RunStatus.RUNNING)
        store.save_run(r)
        assert store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY) is None

    def test_allows_after_terminal(self, store, resource):
        r = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.COMPLETED,
        )
        store.save_run(r)
        second = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        assert second is not None

    def test_get_active_run_id_returns_pending(self, store, resource):
        pending = store.create_pending_run_if_no_active(resource.id, RunType.DISCOVERY)
        assert store.get_active_run_id_for_resource(resource.id) == pending.id

    def test_get_active_run_id_none_when_terminal_only(self, store, resource):
        r = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.COMPLETED,
        )
        store.save_run(r)
        assert store.get_active_run_id_for_resource(resource.id) is None


# ── Route integration tests ───────────────────────────────────────


@pytest.fixture
def api_app(store):
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(api_router)
    app.state.store = store
    # Mock engine — it just needs the async trigger methods to be awaitable.
    mock_engine = MagicMock()

    async def _noop_async(*args, **kwargs):
        # Simulate engine completing the run quickly.
        run_id = kwargs.get("run_id")
        if run_id:
            existing = store.get_run(run_id)
            if existing:
                existing.status = RunStatus.COMPLETED
                store.save_run(existing)

    mock_engine.run_discovery_async = _noop_async
    mock_engine.run_health_check_async = _noop_async
    app.state.engine = mock_engine
    app.state.scheduler = MagicMock()
    return app


@pytest.fixture
def api_client(api_app, store):
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="test")
    return TestClient(api_app, headers={"x-api-key": raw_key})


class TestApiTriggerEndpoints:
    def test_post_runs_returns_run_id_that_matches_executed_run(self, api_client, store, resource):
        r = api_client.post(
            "/api/v1/runs",
            json={"resource_id": resource.id, "run_type": "health_check"},
        )
        assert r.status_code == 200, r.text
        returned_id = r.json()["run_id"]
        # Give the background task a moment to complete.
        for _ in range(20):
            run = store.get_run(returned_id)
            if run and run.status == RunStatus.COMPLETED:
                break
            asyncio.run(asyncio.sleep(0.05))
        run = store.get_run(returned_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        # No orphan: exactly one run exists for this resource.
        all_runs = store.get_runs(resource.id, limit=100)
        assert len(all_runs) == 1

    def test_post_resource_discover_returns_run_id(self, api_client, store, resource):
        r = api_client.post(f"/api/v1/resources/{resource.id}/discover")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "run_id" in body
        # No duplicate row
        runs = store.get_runs(resource.id, limit=100)
        assert len(runs) == 1
        assert runs[0].id == body["run_id"]

    def test_post_resource_health_check_returns_run_id(self, api_client, store, resource):
        r = api_client.post(f"/api/v1/resources/{resource.id}/health-check")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "run_id" in body
        runs = store.get_runs(resource.id, limit=100)
        assert len(runs) == 1
        assert runs[0].id == body["run_id"]

    def test_post_resource_discover_returns_409_when_run_in_flight(self, api_client, store, resource):
        # Manually create a PENDING run that won't auto-complete.
        existing = Run(
            resource_id=resource.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.PENDING,
        )
        store.save_run(existing)
        r = api_client.post(f"/api/v1/resources/{resource.id}/discover")
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "run_in_flight"
        assert detail["active_run_id"] == existing.id

    def test_post_resource_health_check_returns_409_when_run_in_flight(self, api_client, store, resource):
        existing = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.RUNNING,
        )
        store.save_run(existing)
        r = api_client.post(f"/api/v1/resources/{resource.id}/health-check")
        assert r.status_code == 409
        assert r.json()["detail"]["active_run_id"] == existing.id

    def test_post_runs_returns_409_when_run_in_flight(self, api_client, store, resource):
        existing = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.PENDING,
        )
        store.save_run(existing)
        r = api_client.post(
            "/api/v1/runs",
            json={"resource_id": resource.id, "run_type": "health_check"},
        )
        assert r.status_code == 409

    def test_post_runs_with_missing_resource_returns_404(self, api_client):
        r = api_client.post(
            "/api/v1/runs",
            json={"resource_id": "nope", "run_type": "health_check"},
        )
        assert r.status_code == 404

    def test_post_resource_discover_with_missing_resource_returns_404(self, api_client):
        r = api_client.post("/api/v1/resources/nope/discover")
        assert r.status_code == 404
