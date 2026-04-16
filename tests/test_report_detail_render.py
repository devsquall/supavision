"""Tests for Workstream A5: report_detail.html structured vs legacy render.

Uses the same fixture pattern as tests/test_dashboard_routes.py — a minimal
FastAPI app with fake auth + the dashboard router — to render /reports/{id}
against a real Store populated with both structured and legacy reports.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.testclient import TestClient

from supavision.db import Store
from supavision.models import Report, Resource, RunType, User
from supavision.models.health import (
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
    RunMetadata,
)
from supavision.web.auth import hash_password
from supavision.web.dashboard import router as dashboard_router


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a5.db")
    yield s
    s.close()


@pytest.fixture
def app(store):
    app = FastAPI()
    admin = User(
        email="a@test.com",
        password_hash=hash_password("Admin1234!"),
        name="A",
        role="admin",
    )
    store.create_user(admin)

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.csrf_token = "csrf"
        request.state.current_user = admin
        request.state.is_admin = True
        return await call_next(request)

    static_dir = Path(__file__).resolve().parent.parent / "src" / "supavision" / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.store = store
    app.state.engine = None
    app.state.scheduler = None
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def _resource(store: Store) -> Resource:
    r = Resource(name="prod-01", resource_type="server")
    store.save_resource(r)
    return r


# ── Legacy render (pre-A reports) ───────────────────────────────────


class TestLegacyReportRender:
    def test_prose_only_report_renders(self, client, store) -> None:
        res = _resource(store)
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="## Some narrative\n\nAll good.",
        )
        store.save_report(r)
        resp = client.get(f"/reports/{r.id}")
        assert resp.status_code == 200
        body = resp.text
        # Legacy badge shows
        assert "legacy" in body
        # Prose body rendered
        assert "Some narrative" in body
        # Structured sections are NOT present
        assert "metrics-strip" not in body
        assert "issue-list" not in body

    def test_404_on_unknown_report(self, client) -> None:
        resp = client.get("/reports/does-not-exist")
        assert resp.status_code == 404


# ── Structured render (A5 main path) ────────────────────────────────


class TestStructuredReportRender:
    def _seed_structured(self, store: Store) -> Report:
        res = _resource(store)
        payload = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="Disk at 82% on /var, growing 4GB/week.",
            metrics={"cpu_percent": 23, "disk_percent": 82, "memory_percent": 61},
            issues=[
                ReportIssue(
                    title="Disk will be full in 28 days",
                    severity=IssueSeverity.WARNING,
                    evidence="df -h: /var 82% used; 4.1GB/week growth",
                    recommendation="Rotate /var/log/app/*.log",
                    tags=["disk", "capacity"],
                    scope="/var",
                ),
                ReportIssue(
                    title="SSH brute-force attempts (142/24h)",
                    severity=IssueSeverity.INFO,
                    tags=["brute-force"],
                    scope="sshd",
                ),
            ],
        )
        metadata = RunMetadata(
            template_version="server/v1",
            tool_calls_made=14,
            runtime_seconds=42.7,
        )
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="raw prose narrative here",
            payload=payload,
            run_metadata=metadata,
        )
        store.save_report(r)
        return r

    def test_structured_report_renders_metrics(self, client, store) -> None:
        r = self._seed_structured(store)
        resp = client.get(f"/reports/{r.id}")
        assert resp.status_code == 200
        body = resp.text
        assert "metric-chip" in body
        assert "cpu_percent" in body
        assert "disk_percent" in body
        assert "82" in body

    def test_structured_report_renders_issues(self, client, store) -> None:
        r = self._seed_structured(store)
        body = client.get(f"/reports/{r.id}").text
        assert "issue-list" in body
        assert "Disk will be full in 28 days" in body
        assert "SSH brute-force attempts" in body
        assert "Rotate /var/log/app/*.log" in body
        assert "df -h" in body
        assert "/var" in body
        # Tags rendered
        assert "brute-force" in body

    def test_structured_report_shows_run_metadata(self, client, store) -> None:
        r = self._seed_structured(store)
        body = client.get(f"/reports/{r.id}").text
        assert "server/v1" in body
        assert "14" in body  # tool_calls_made
        assert "42.7" in body  # runtime_seconds

    def test_structured_report_keeps_raw_in_details(self, client, store) -> None:
        r = self._seed_structured(store)
        body = client.get(f"/reports/{r.id}").text
        # Raw narrative is collapsed inside <details>
        assert "<details" in body
        assert "raw prose narrative here" in body

    def test_legacy_badge_not_shown_for_structured(self, client, store) -> None:
        r = self._seed_structured(store)
        body = client.get(f"/reports/{r.id}").text
        # "legacy" badge must NOT be visible on structured reports
        # (the word might appear incidentally elsewhere, so check the class)
        assert "badge--muted" not in body or ">legacy<" not in body

    def test_empty_issues_list_shows_no_issues_message(self, client, store) -> None:
        res = _resource(store)
        payload = ReportPayload(
            status=PayloadStatus.HEALTHY,
            summary="All good.",
            metrics={"cpu_percent": 12},
            issues=[],
        )
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="fine",
            payload=payload,
        )
        store.save_report(r)
        body = client.get(f"/reports/{r.id}").text
        assert "No issues reported" in body
