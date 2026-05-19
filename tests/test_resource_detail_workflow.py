"""Regression test for the resource-detail workflow-state bug (item 4.9).

The handler used to derive `has_discovery` / `has_health_check` from the
paginated `recent_runs` slice (first 10 by default). A resource with many
health-check runs since its last discovery would have the discovery run sit
on page 2+, and the workflow tracker would incorrectly say "needs discovery".

Fix: use `store.get_latest_run(resource_id, run_type)` — dedicated indexed
lookups that don't depend on pagination.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    s = Store(tmp_path / "test_workflow.db")
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
    app.state.engine = None
    app.state.scheduler = MagicMock()
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def client(store):
    return TestClient(_make_app(store), raise_server_exceptions=False)


def _completed_run(resource_id: str, run_type: RunType, created_at: datetime) -> Run:
    return Run(
        resource_id=resource_id,
        run_type=run_type,
        status=RunStatus.COMPLETED,
        started_at=created_at,
        completed_at=created_at + timedelta(seconds=10),
        created_at=created_at,
    )


class TestWorkflowStateUsesLatestRun:
    def test_workflow_step_is_healthy_when_discovery_outside_page_one(self, client, store):
        """Seed a resource whose successful discovery is older than page 1 of
        health-check runs. The workflow tracker must still recognise
        `has_discovery=True` and render the healthy/post-baseline state.
        """
        r = Resource(name="busy", resource_type="server")
        store.save_resource(r)

        now = datetime.now(timezone.utc)
        # One old completed discovery (will be pushed off page 1).
        store.save_run(_completed_run(r.id, RunType.DISCOVERY, now - timedelta(days=30)))
        # 15 more recent health-check runs (page size is 10).
        for i in range(15):
            store.save_run(_completed_run(r.id, RunType.HEALTH_CHECK, now - timedelta(days=14 - i)))

        resp = client.get(f"/resources/{r.id}")
        assert resp.status_code == 200, resp.text
        # The new workflow CSS classes are: workflow-step--done for completed
        # steps. We assert that BOTH "Discovery Baseline" and "Health Check"
        # are rendered as done (i.e. the bug is gone — neither says
        # workflow-step--current under needs_discovery).
        assert 'data-workflow-step="healthy"' in resp.text, (
            "expected workflow_step=healthy after old discovery + many health checks "
            "(before fix, this read needs_discovery because the discovery row was "
            "off page 1 of recent_runs)"
        )

    def test_workflow_step_is_needs_discovery_when_truly_never_baselined(self, client, store):
        r = Resource(name="fresh", resource_type="server")
        store.save_resource(r)
        # No runs at all.

        resp = client.get(f"/resources/{r.id}")
        assert resp.status_code == 200, resp.text
        assert 'data-workflow-step="needs_discovery"' in resp.text

    def test_workflow_step_is_needs_health_check_after_first_discovery(self, client, store):
        r = Resource(name="onboarding", resource_type="server")
        store.save_resource(r)
        store.save_run(_completed_run(r.id, RunType.DISCOVERY, datetime.now(timezone.utc) - timedelta(minutes=5)))

        resp = client.get(f"/resources/{r.id}")
        assert resp.status_code == 200, resp.text
        assert 'data-workflow-step="needs_health_check"' in resp.text
