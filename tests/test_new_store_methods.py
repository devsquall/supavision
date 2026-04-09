"""Tests for new cross-resource store methods added for dashboard pages."""

from datetime import datetime, timedelta, timezone

import pytest

from supavision.db import Store
from supavision.models import (
    AgentJob,
    Finding,
    FindingSeverity,
    FindingStage,
    JobStatus,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
    Transition,
)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "test.db"))


@pytest.fixture
def seeded_store(store):
    """Store with resources, reports, runs, jobs, notifications, and auth events."""
    now = datetime.now(timezone.utc)

    # Two resources
    r1 = Resource(name="server-1", resource_type="server")
    r2 = Resource(name="codebase-1", resource_type="codebase", config={"path": "/tmp"})
    store.save_resource(r1)
    store.save_resource(r2)

    # Reports for r1
    for i, rt in enumerate(["health_check", "discovery", "health_check"]):
        rp = Report(
            resource_id=r1.id,
            run_type=rt,
            content=f"Report {i} content for {rt}",
        )
        rp.created_at = now - timedelta(days=i)
        store.save_report(rp)

    # A report for r2
    rp2 = Report(resource_id=r2.id, run_type="health_check", content="Codebase report")
    store.save_report(rp2)

    # Runs for r1
    for i, status in enumerate([RunStatus.COMPLETED, RunStatus.RUNNING, RunStatus.FAILED]):
        run = Run(
            resource_id=r1.id,
            run_type=RunType.HEALTH_CHECK,
            status=status,
            started_at=now - timedelta(hours=i * 2),
        )
        if status == RunStatus.COMPLETED:
            run.completed_at = run.started_at + timedelta(seconds=60)
        store.save_run(run)

    # Agent jobs
    f1 = Finding(
        resource_id=r2.id,
        file_path="src/query.py",
        line_number=42,
        category="sql-injection",
        severity=FindingSeverity.HIGH,
        stage=FindingStage.EVALUATED,
        language="python",
        snippet="cursor.execute(f'SELECT * FROM users WHERE id={user_id}')",
    )
    store.save_work_item(f1)

    for jtype, jstatus in [("evaluate", JobStatus.COMPLETED), ("implement", JobStatus.RUNNING)]:
        job = AgentJob(
            work_item_id=f1.id,
            resource_id=r2.id,
            job_type=jtype,
            status=jstatus,
        )
        store.save_agent_job(job)

    # Notifications
    store.log_notification(r1.id, "slack", "critical", "Disk full", "sent", "")
    store.log_notification(r1.id, "webhook", "warning", "High load", "failed", "Timeout")
    store.log_notification(r2.id, "slack", "info", "Scan complete", "sent", "")

    # Auth events
    store.log_auth_event("login_success", email="admin@test.com", ip_address="127.0.0.1")
    store.log_auth_event("login_failure", email="hacker@bad.com", ip_address="10.0.0.1")

    # Transitions
    t = Transition(
        work_item_id=f1.id,
        from_stage="scanned",
        to_stage="evaluated",
    )
    store.save_transition(t)

    return store, r1, r2, f1


class TestListAllReports:
    def test_returns_all_reports(self, seeded_store):
        store, r1, r2, _ = seeded_store
        reports, total = store.list_all_reports()
        assert total == 4
        assert len(reports) == 4

    def test_filter_by_resource(self, seeded_store):
        store, r1, r2, _ = seeded_store
        reports, total = store.list_all_reports(resource_id=r1.id)
        assert total == 3
        assert all(r["resource_id"] == r1.id for r in reports)

    def test_filter_by_run_type(self, seeded_store):
        store, r1, r2, _ = seeded_store
        reports, total = store.list_all_reports(run_type="discovery")
        assert total == 1
        assert reports[0]["run_type"] == "discovery"

    def test_pagination(self, seeded_store):
        store, r1, r2, _ = seeded_store
        page1, total = store.list_all_reports(limit=2, offset=0)
        assert total == 4
        assert len(page1) == 2
        page2, _ = store.list_all_reports(limit=2, offset=2)
        assert len(page2) == 2
        # No overlap
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_includes_resource_name(self, seeded_store):
        store, r1, r2, _ = seeded_store
        reports, _ = store.list_all_reports(resource_id=r1.id, limit=1)
        assert reports[0]["resource_name"] == "server-1"

    def test_includes_preview(self, seeded_store):
        store, _, _, _ = seeded_store
        reports, _ = store.list_all_reports(limit=1)
        assert "preview" in reports[0]
        assert len(reports[0]["preview"]) > 0

    def test_empty_store(self, store):
        reports, total = store.list_all_reports()
        assert total == 0
        assert reports == []


class TestListRecentRuns:
    def test_returns_all_runs(self, seeded_store):
        store, _, _, _ = seeded_store
        runs, total = store.list_recent_runs()
        assert total == 3
        assert len(runs) == 3

    def test_filter_by_status(self, seeded_store):
        store, _, _, _ = seeded_store
        runs, total = store.list_recent_runs(status="running")
        assert total == 1
        assert runs[0].status == RunStatus.RUNNING

    def test_filter_by_run_type(self, seeded_store):
        store, _, _, _ = seeded_store
        runs, total = store.list_recent_runs(run_type="health_check")
        assert total == 3

    def test_pagination(self, seeded_store):
        store, _, _, _ = seeded_store
        runs, total = store.list_recent_runs(limit=2, offset=0)
        assert total == 3
        assert len(runs) == 2

    def test_empty(self, store):
        runs, total = store.list_recent_runs()
        assert total == 0


class TestListAllAgentJobs:
    def test_returns_all_jobs(self, seeded_store):
        store, _, _, _ = seeded_store
        jobs, total = store.list_all_agent_jobs()
        assert total == 2
        assert len(jobs) == 2

    def test_filter_by_status(self, seeded_store):
        store, _, _, _ = seeded_store
        jobs, total = store.list_all_agent_jobs(status="running")
        assert total == 1

    def test_filter_by_job_type(self, seeded_store):
        store, _, _, _ = seeded_store
        jobs, total = store.list_all_agent_jobs(job_type="evaluate")
        assert total == 1
        assert jobs[0].job_type == "evaluate"

    def test_combined_filters(self, seeded_store):
        store, _, _, _ = seeded_store
        jobs, total = store.list_all_agent_jobs(status="completed", job_type="evaluate")
        assert total == 1

    def test_empty(self, store):
        jobs, total = store.list_all_agent_jobs()
        assert total == 0


class TestListAuthEvents:
    def test_returns_events(self, seeded_store):
        store, _, _, _ = seeded_store
        events = store.list_auth_events()
        assert len(events) == 2

    def test_limit(self, seeded_store):
        store, _, _, _ = seeded_store
        events = store.list_auth_events(limit=1)
        assert len(events) == 1

    def test_offset(self, seeded_store):
        store, _, _, _ = seeded_store
        events = store.list_auth_events(limit=1, offset=1)
        assert len(events) == 1

    def test_empty(self, store):
        events = store.list_auth_events()
        assert events == []


class TestListNotificationsExtended:
    def test_returns_all(self, seeded_store):
        store, _, _, _ = seeded_store
        notifs, total = store.list_notifications_extended()
        assert total == 3
        assert len(notifs) == 3

    def test_filter_by_resource(self, seeded_store):
        store, r1, r2, _ = seeded_store
        notifs, total = store.list_notifications_extended(resource_id=r1.id)
        assert total == 2

    def test_filter_by_severity(self, seeded_store):
        store, _, _, _ = seeded_store
        notifs, total = store.list_notifications_extended(severity="critical")
        assert total == 1

    def test_filter_by_channel(self, seeded_store):
        store, _, _, _ = seeded_store
        notifs, total = store.list_notifications_extended(channel="webhook")
        assert total == 1

    def test_filter_by_status(self, seeded_store):
        store, _, _, _ = seeded_store
        notifs, total = store.list_notifications_extended(status="failed")
        assert total == 1
        assert notifs[0]["error"] == "Timeout"

    def test_pagination(self, seeded_store):
        store, _, _, _ = seeded_store
        notifs, total = store.list_notifications_extended(limit=2, offset=0)
        assert total == 3
        assert len(notifs) == 2

    def test_combined_filters(self, seeded_store):
        store, r1, _, _ = seeded_store
        notifs, total = store.list_notifications_extended(
            resource_id=r1.id, channel="slack"
        )
        assert total == 1

    def test_empty(self, store):
        notifs, total = store.list_notifications_extended()
        assert total == 0


class TestListRecentTransitions:
    def test_returns_transitions(self, seeded_store):
        store, _, _, f1 = seeded_store
        transitions = store.list_recent_transitions()
        assert len(transitions) == 1
        assert transitions[0]["work_item_id"] == f1.id
        assert transitions[0]["from_stage"] == "scanned"
        assert transitions[0]["to_stage"] == "evaluated"

    def test_includes_title(self, seeded_store):
        store, _, _, _ = seeded_store
        transitions = store.list_recent_transitions()
        assert transitions[0]["title"]  # Non-empty

    def test_empty(self, store):
        transitions = store.list_recent_transitions()
        assert transitions == []
