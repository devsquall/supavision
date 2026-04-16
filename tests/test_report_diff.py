"""Tests for Workstream A6: run-vs-previous issue diff.

Covers the pure `compute_issue_diff` function, engine-level diff stamping
on stored reports, and the template rendering of the diff panel.
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
    IssueDiff,
    IssueDiffEntry,
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
    compute_issue_diff,
)
from supavision.web.auth import hash_password
from supavision.web.dashboard import router as dashboard_router

# ── Pure set-diff tests ──────────────────────────────────────────────


def _issue(tag: str, scope: str, title: str = "x", sev: IssueSeverity = IssueSeverity.WARNING) -> ReportIssue:
    return ReportIssue(title=title, severity=sev, tags=[tag], scope=scope)


def _payload(*issues: ReportIssue, status: PayloadStatus = PayloadStatus.WARNING) -> ReportPayload:
    return ReportPayload(status=status, summary="test", issues=list(issues))


class TestComputeIssueDiff:
    def test_previous_none_all_new(self) -> None:
        curr = _payload(_issue("disk", "/var"), _issue("memory", "host"))
        diff = compute_issue_diff(curr, None)
        assert len(diff.new) == 2
        assert diff.resolved == []
        assert diff.persisted == []
        assert diff.compared_against_report_id is None

    def test_empty_current_all_resolved(self) -> None:
        curr = _payload(status=PayloadStatus.HEALTHY)
        prev = _payload(_issue("disk", "/var"))
        diff = compute_issue_diff(curr, prev)
        assert diff.new == []
        assert len(diff.resolved) == 1
        assert diff.resolved[0].id == "disk-var"
        assert diff.persisted == []

    def test_full_overlap_all_persisted(self) -> None:
        a = _issue("disk", "/var", title="v1")
        b = _issue("disk", "/var", title="v2 reworded")
        curr = _payload(b)
        prev = _payload(a)
        diff = compute_issue_diff(curr, prev)
        assert diff.new == []
        assert diff.resolved == []
        assert len(diff.persisted) == 1
        # Persisted uses the *current* title
        assert diff.persisted[0].title == "v2 reworded"

    def test_mixed(self) -> None:
        curr = _payload(
            _issue("disk", "/var", title="Disk warning"),
            _issue("memory", "host", title="Mem warning"),
        )
        prev = _payload(
            _issue("disk", "/var", title="Disk — old wording"),
            _issue("service", "nginx", title="nginx restart"),
        )
        diff = compute_issue_diff(curr, prev, compared_against_report_id="prev-id")
        assert {e.id for e in diff.new} == {"memory-host"}
        assert {e.id for e in diff.resolved} == {"service-nginx"}
        assert {e.id for e in diff.persisted} == {"disk-var"}
        assert diff.compared_against_report_id == "prev-id"

    def test_has_changes_true_on_new(self) -> None:
        d = IssueDiff(new=[])
        assert d.has_changes is False
        d2 = compute_issue_diff(_payload(_issue("disk", "/var")), _payload())
        assert d2.has_changes is True

    def test_has_changes_true_on_resolved(self) -> None:
        d = compute_issue_diff(_payload(), _payload(_issue("disk", "/var")))
        assert d.has_changes is True

    def test_has_changes_false_on_pure_persist(self) -> None:
        same = _issue("disk", "/var")
        d = compute_issue_diff(_payload(same), _payload(same))
        assert d.has_changes is False

    def test_total_current(self) -> None:
        curr = _payload(_issue("disk", "/var"), _issue("memory", "host"))
        prev = _payload(_issue("disk", "/var"), _issue("service", "nginx"))
        d = compute_issue_diff(curr, prev)
        # new = {memory-host}, persisted = {disk-var}
        assert d.total_current == 2

    def test_title_drift_still_persists(self) -> None:
        # R6: the same logical issue with reworded title must persist, not churn.
        curr = _payload(_issue("disk", "/var", title="Disk almost full"))
        prev = _payload(_issue("disk", "/var", title="Disk filling up"))
        d = compute_issue_diff(curr, prev)
        assert d.new == []
        assert d.resolved == []
        assert len(d.persisted) == 1


# ── Engine-level diff stamping via store roundtrip ──────────────────


class TestDiffOnReport:
    def test_report_roundtrip_with_diff(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "diff.db")
        res = Resource(name="x", resource_type="server")
        store.save_resource(res)

        diff = IssueDiff(
            new=[],
            resolved=[],
            persisted=[],
            compared_against_report_id="prev-id",
        )
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="prose",
            payload=_payload(),
            payload_diff=diff,
        )
        store.save_report(r)
        loaded = store.get_report(r.id)
        assert loaded is not None
        assert loaded.payload_diff is not None
        assert loaded.payload_diff.compared_against_report_id == "prev-id"
        store.close()

    def test_legacy_report_has_no_diff(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "legacy.db")
        res = Resource(name="x", resource_type="server")
        store.save_resource(res)
        r = Report(resource_id=res.id, run_type=RunType.HEALTH_CHECK, content="legacy")
        store.save_report(r)
        loaded = store.get_report(r.id)
        assert loaded is not None
        assert loaded.payload_diff is None
        store.close()


# ── Template rendering of diff panel ────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a6.db")
    yield s
    s.close()


@pytest.fixture
def client(store):
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
    return TestClient(app, raise_server_exceptions=False)


class TestDiffRendering:
    def _seed_with_diff(self, store: Store) -> Report:
        res = Resource(name="srv", resource_type="server")
        store.save_resource(res)

        curr_payload = _payload(
            _issue("disk", "/var", title="Disk almost full"),
            _issue("memory", "host", title="Memory pressure rising"),
        )
        diff = IssueDiff(
            new=[
                IssueDiffEntry(
                    id="memory-host",
                    title="Memory pressure rising",
                    severity=IssueSeverity.WARNING,
                ),
            ],
            resolved=[
                IssueDiffEntry(
                    id="service-nginx",
                    title="nginx restart loop resolved",
                    severity=IssueSeverity.CRITICAL,
                ),
            ],
            persisted=[
                IssueDiffEntry(
                    id="disk-var",
                    title="Disk almost full",
                    severity=IssueSeverity.WARNING,
                ),
            ],
            compared_against_report_id="prev",
        )
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="prose",
            payload=curr_payload,
            payload_diff=diff,
        )
        store.save_report(r)
        return r

    def test_diff_panel_renders(self, client, store) -> None:
        r = self._seed_with_diff(store)
        body = client.get(f"/reports/{r.id}").text
        assert "What changed since last run" in body
        assert "+1 new" in body
        assert "1 resolved" in body  # "−1 resolved" — minus is HTML entity
        assert "=1 persisted" in body

    def test_diff_new_titles_listed(self, client, store) -> None:
        r = self._seed_with_diff(store)
        body = client.get(f"/reports/{r.id}").text
        assert "Memory pressure rising" in body

    def test_diff_resolved_titles_listed(self, client, store) -> None:
        r = self._seed_with_diff(store)
        body = client.get(f"/reports/{r.id}").text
        assert "nginx restart loop resolved" in body

    def test_no_diff_panel_on_legacy_report(self, client, store) -> None:
        res = Resource(name="legacy-srv", resource_type="server")
        store.save_resource(res)
        r = Report(resource_id=res.id, run_type=RunType.HEALTH_CHECK, content="old")
        store.save_report(r)
        body = client.get(f"/reports/{r.id}").text
        assert "What changed since last run" not in body
