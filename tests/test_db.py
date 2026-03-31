"""Tests for db.py — SQLite storage layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from supervisor.db import Store
from supervisor.models import (
    Checklist,
    ChecklistItem,
    Evaluation,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
    Severity,
    SystemContext,
)


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temp database."""
    db_path = tmp_path / "test.db"
    s = Store(db_path)
    yield s
    s.close()


# ── Helper factories ─────────────────────────────────────────────


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


def _make_context(resource_id: str, version: int = 1, content: str = "context data") -> SystemContext:
    return SystemContext(resource_id=resource_id, content=content, version=version)


def _make_checklist(resource_id: str, version: int = 1) -> Checklist:
    items = [
        ChecklistItem(description="Check disk usage", source="discovery"),
        ChecklistItem(description="Verify nginx running", source="discovery"),
    ]
    return Checklist(resource_id=resource_id, items=items, version=version)


def _make_report(resource_id: str, run_type: RunType = RunType.HEALTH_CHECK) -> Report:
    return Report(resource_id=resource_id, run_type=run_type, content="Report content here")


def _make_evaluation(report_id: str, resource_id: str) -> Evaluation:
    return Evaluation(
        report_id=report_id,
        resource_id=resource_id,
        severity=Severity.HEALTHY,
        summary="All good",
        should_alert=False,
    )


def _make_run(
    resource_id: str,
    run_type: RunType = RunType.HEALTH_CHECK,
    status: RunStatus = RunStatus.PENDING,
) -> Run:
    return Run(resource_id=resource_id, run_type=run_type, status=status)


# ── Resource CRUD ────────────────────────────────────────────────


class TestResourceCRUD:
    def test_save_and_get_resource(self, store):
        r = _make_resource(name="prod-server")
        store.save_resource(r)
        got = store.get_resource(r.id)
        assert got is not None
        assert got.name == "prod-server"
        assert got.resource_type == "server"
        assert got.id == r.id

    def test_get_nonexistent_resource_returns_none(self, store):
        assert store.get_resource("nonexistent-id") is None

    def test_list_resources_empty(self, store):
        assert store.list_resources() == []

    def test_list_resources_returns_all(self, store):
        r1 = _make_resource(name="server-1")
        r2 = _make_resource(name="server-2")
        store.save_resource(r1)
        store.save_resource(r2)
        resources = store.list_resources()
        assert len(resources) == 2
        names = {r.name for r in resources}
        assert names == {"server-1", "server-2"}

    def test_list_resources_by_parent_id(self, store):
        parent = _make_resource(name="parent")
        child1 = _make_resource(name="child-1", parent_id=parent.id)
        child2 = _make_resource(name="child-2", parent_id=parent.id)
        orphan = _make_resource(name="orphan")

        store.save_resource(parent)
        store.save_resource(child1)
        store.save_resource(child2)
        store.save_resource(orphan)

        children = store.list_resources(parent_id=parent.id)
        assert len(children) == 2
        names = {r.name for r in children}
        assert names == {"child-1", "child-2"}

    def test_delete_resource(self, store):
        r = _make_resource()
        store.save_resource(r)
        assert store.get_resource(r.id) is not None
        store.delete_resource(r.id)
        assert store.get_resource(r.id) is None

    def test_delete_nonexistent_resource_does_not_error(self, store):
        # Should not raise
        store.delete_resource("nonexistent")

    def test_save_resource_upserts(self, store):
        r = _make_resource(name="original")
        store.save_resource(r)
        r.name = "updated"
        # Re-serialize the updated model
        r_updated = Resource(
            id=r.id, name="updated", resource_type=r.resource_type
        )
        store.save_resource(r_updated)
        got = store.get_resource(r.id)
        assert got.name == "updated"

    def test_resource_tree(self, store):
        grandparent = _make_resource(name="grandparent")
        parent = _make_resource(name="parent", parent_id=grandparent.id)
        child = _make_resource(name="child", parent_id=parent.id)

        store.save_resource(grandparent)
        store.save_resource(parent)
        store.save_resource(child)

        tree = store.get_resource_tree(child.id)
        assert len(tree) == 3
        # Tree should be ordered root → ... → self
        assert tree[0].name == "grandparent"
        assert tree[1].name == "parent"
        assert tree[2].name == "child"


# ── System Context versioning ────────────────────────────────────


class TestSystemContext:
    def test_save_and_get_latest(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        ctx1 = _make_context(resource.id, version=1, content="v1 content")
        store.save_context(ctx1)

        latest = store.get_latest_context(resource.id)
        assert latest is not None
        assert latest.version == 1
        assert latest.content == "v1 content"

    def test_latest_returns_highest_version(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        ctx1 = _make_context(resource.id, version=1, content="v1")
        ctx2 = _make_context(resource.id, version=2, content="v2")
        ctx3 = _make_context(resource.id, version=3, content="v3")
        store.save_context(ctx1)
        store.save_context(ctx2)
        store.save_context(ctx3)

        latest = store.get_latest_context(resource.id)
        assert latest.version == 3
        assert latest.content == "v3"

    def test_get_latest_returns_none_for_missing(self, store):
        assert store.get_latest_context("nonexistent") is None

    def test_context_history(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(1, 6):
            ctx = _make_context(resource.id, version=i, content=f"v{i}")
            store.save_context(ctx)

        history = store.get_context_history(resource.id, limit=3)
        assert len(history) == 3
        # Should be in descending version order
        assert history[0].version == 5
        assert history[1].version == 4
        assert history[2].version == 3

    def test_context_history_respects_limit(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(1, 11):
            store.save_context(_make_context(resource.id, version=i))

        history = store.get_context_history(resource.id, limit=2)
        assert len(history) == 2


# ── Checklists ───────────────────────────────────────────────────


class TestChecklist:
    def test_save_and_get_latest(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        cl = _make_checklist(resource.id)
        store.save_checklist(cl)

        got = store.get_latest_checklist(resource.id)
        assert got is not None
        assert len(got.items) == 2
        assert got.items[0].description == "Check disk usage"

    def test_latest_checklist_by_version(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        cl1 = _make_checklist(resource.id, version=1)
        cl2 = Checklist(
            resource_id=resource.id,
            items=[ChecklistItem(description="New item", source="discovery")],
            version=2,
        )
        store.save_checklist(cl1)
        store.save_checklist(cl2)

        latest = store.get_latest_checklist(resource.id)
        assert latest.version == 2
        assert len(latest.items) == 1
        assert latest.items[0].description == "New item"

    def test_get_latest_checklist_returns_none_for_missing(self, store):
        assert store.get_latest_checklist("nonexistent") is None


# ── Reports ──────────────────────────────────────────────────────


class TestReports:
    def test_save_and_get_report(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        report = _make_report(resource.id)
        store.save_report(report)

        got = store.get_report(report.id)
        assert got is not None
        assert got.content == "Report content here"
        assert got.run_type == RunType.HEALTH_CHECK

    def test_get_nonexistent_report(self, store):
        assert store.get_report("nonexistent") is None

    def test_get_recent_reports(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(5):
            report = Report(
                resource_id=resource.id,
                run_type=RunType.HEALTH_CHECK,
                content=f"Report {i}",
                created_at=datetime.now(timezone.utc) + timedelta(seconds=i),
            )
            store.save_report(report)

        recent = store.get_recent_reports(resource.id, RunType.HEALTH_CHECK, limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].content == "Report 4"

    def test_recent_reports_filters_by_run_type(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for rt in [RunType.HEALTH_CHECK, RunType.DISCOVERY, RunType.HEALTH_CHECK]:
            store.save_report(_make_report(resource.id, rt))

        hc_reports = store.get_recent_reports(resource.id, RunType.HEALTH_CHECK)
        assert len(hc_reports) == 2

        disc_reports = store.get_recent_reports(resource.id, RunType.DISCOVERY)
        assert len(disc_reports) == 1


# ── Evaluations ──────────────────────────────────────────────────


class TestEvaluations:
    def test_save_and_get_evaluation(self, store):
        resource = _make_resource()
        store.save_resource(resource)
        report = _make_report(resource.id)
        store.save_report(report)

        evaluation = _make_evaluation(report.id, resource.id)
        store.save_evaluation(evaluation)

        got = store.get_evaluation(evaluation.id)
        assert got is not None
        assert got.severity == Severity.HEALTHY
        assert got.summary == "All good"

    def test_get_nonexistent_evaluation(self, store):
        assert store.get_evaluation("nonexistent") is None

    def test_get_recent_evaluations(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(5):
            report = _make_report(resource.id)
            store.save_report(report)
            ev = Evaluation(
                report_id=report.id,
                resource_id=resource.id,
                severity=Severity.HEALTHY,
                summary=f"Eval {i}",
                created_at=datetime.now(timezone.utc) + timedelta(seconds=i),
            )
            store.save_evaluation(ev)

        recent = store.get_recent_evaluations(resource.id, limit=3)
        assert len(recent) == 3
        assert recent[0].summary == "Eval 4"


# ── Run lifecycle ────────────────────────────────────────────────


class TestRuns:
    def test_save_and_get_run(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        run = _make_run(resource.id)
        store.save_run(run)

        got = store.get_run(run.id)
        assert got is not None
        assert got.status == RunStatus.PENDING
        assert got.run_type == RunType.HEALTH_CHECK

    def test_get_nonexistent_run(self, store):
        assert store.get_run("nonexistent") is None

    def test_run_status_update(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        run = _make_run(resource.id)
        store.save_run(run)

        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        store.save_run(run)

        got = store.get_run(run.id)
        assert got.status == RunStatus.RUNNING
        assert got.started_at is not None

    def test_get_pending_runs(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        run1 = _make_run(resource.id, status=RunStatus.PENDING)
        run2 = _make_run(resource.id, status=RunStatus.RUNNING)
        run3 = _make_run(resource.id, status=RunStatus.PENDING)
        store.save_run(run1)
        store.save_run(run2)
        store.save_run(run3)

        pending = store.get_pending_runs()
        assert len(pending) == 2
        statuses = {r.status for r in pending}
        assert statuses == {RunStatus.PENDING}

    def test_get_runs_for_resource(self, store):
        r1 = _make_resource(name="r1")
        r2 = _make_resource(name="r2")
        store.save_resource(r1)
        store.save_resource(r2)

        store.save_run(_make_run(r1.id))
        store.save_run(_make_run(r1.id))
        store.save_run(_make_run(r2.id))

        r1_runs = store.get_runs(r1.id)
        assert len(r1_runs) == 2

    def test_get_runs_limit(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        for _ in range(10):
            store.save_run(_make_run(resource.id))

        runs = store.get_runs(resource.id, limit=3)
        assert len(runs) == 3

    def test_get_latest_run(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        run1 = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        run2 = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            created_at=datetime.now(timezone.utc),
        )
        store.save_run(run1)
        store.save_run(run2)

        latest = store.get_latest_run(resource.id, RunType.HEALTH_CHECK)
        assert latest.id == run2.id

    def test_get_latest_run_none_for_missing(self, store):
        assert store.get_latest_run("nonexistent", RunType.DISCOVERY) is None

    def test_stale_runs(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        # Old running run (5 hours old)
        old_run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        store.save_run(old_run)

        # Recent running run (1 hour old)
        recent_run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        store.save_run(recent_run)

        # Completed run (should never appear)
        completed_run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(timezone.utc) - timedelta(hours=10),
        )
        store.save_run(completed_run)

        stale = store.get_stale_runs(hours=4)
        assert len(stale) == 1
        assert stale[0].id == old_run.id

    def test_get_latest_runs_batch(self, store):
        r1 = _make_resource(name="r1")
        r2 = _make_resource(name="r2")
        store.save_resource(r1)
        store.save_resource(r2)

        # Completed runs for r1
        run1 = Run(
            resource_id=r1.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        run2 = Run(
            resource_id=r1.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        store.save_run(run1)
        store.save_run(run2)

        # Completed run for r2
        run3 = Run(
            resource_id=r2.id,
            run_type=RunType.DISCOVERY,
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        store.save_run(run3)

        batch = store.get_latest_runs_batch()
        assert (r1.id, str(RunType.HEALTH_CHECK)) in batch
        assert batch[(r1.id, str(RunType.HEALTH_CHECK))].id == run2.id
        assert (r2.id, str(RunType.DISCOVERY)) in batch
        assert batch[(r2.id, str(RunType.DISCOVERY))].id == run3.id


# ── API Key operations ───────────────────────────────────────────


class TestAPIKeys:
    def test_save_and_validate_api_key(self, store):
        key_id = str(uuid4())
        key_hash = "abc123hash"
        store.save_api_key(key_id, key_hash, label="test key")

        result = store.validate_api_key(key_hash)
        assert result is not None
        assert result["id"] == key_id
        assert result["label"] == "test key"

    def test_validate_nonexistent_key(self, store):
        assert store.validate_api_key("nonexistent_hash") is None

    def test_revoke_api_key(self, store):
        key_id = str(uuid4())
        key_hash = "abc123hash"
        store.save_api_key(key_id, key_hash, label="test key")

        # Validate before revoke
        assert store.validate_api_key(key_hash) is not None

        # Revoke
        result = store.revoke_api_key(key_id)
        assert result is True

        # Validate after revoke should return None
        assert store.validate_api_key(key_hash) is None

    def test_revoke_already_revoked(self, store):
        key_id = str(uuid4())
        store.save_api_key(key_id, "hash1", label="key")
        assert store.revoke_api_key(key_id) is True
        # Second revoke returns False (already revoked)
        assert store.revoke_api_key(key_id) is False

    def test_revoke_nonexistent_key(self, store):
        assert store.revoke_api_key("nonexistent") is False

    def test_list_api_keys(self, store):
        store.save_api_key("id1", "hash1", label="key-1")
        store.save_api_key("id2", "hash2", label="key-2")

        keys = store.list_api_keys()
        assert len(keys) == 2
        labels = {k["label"] for k in keys}
        assert labels == {"key-1", "key-2"}

    def test_list_api_keys_shows_revoked_status(self, store):
        store.save_api_key("id1", "hash1", label="active")
        store.save_api_key("id2", "hash2", label="revoked")
        store.revoke_api_key("id2")

        keys = store.list_api_keys()
        by_label = {k["label"]: k for k in keys}
        assert by_label["active"]["revoked"] is False
        assert by_label["revoked"]["revoked"] is True

    def test_list_api_keys_empty(self, store):
        assert store.list_api_keys() == []


# ── Purge operations ─────────────────────────────────────────────


class TestPurge:
    def test_purge_old_reports(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        # Old report (100 days ago)
        old_report = Report(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            content="old",
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        store.save_report(old_report)

        # Old evaluation linked to old report
        old_eval = Evaluation(
            report_id=old_report.id,
            resource_id=resource.id,
            severity=Severity.HEALTHY,
            summary="old eval",
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        store.save_evaluation(old_eval)

        # Recent report (1 day ago)
        recent_report = Report(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            content="recent",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        store.save_report(recent_report)

        count = store.purge_old_reports(days=90)
        assert count == 1

        # Old report gone
        assert store.get_report(old_report.id) is None
        # Old evaluation also gone
        assert store.get_evaluation(old_eval.id) is None
        # Recent report still exists
        assert store.get_report(recent_report.id) is not None

    def test_purge_old_runs(self, store):
        resource = _make_resource()
        store.save_resource(resource)

        # Old completed run
        old_run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        store.save_run(old_run)

        # Old running run (should NOT be purged — only completed/failed)
        old_running = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.RUNNING,
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        store.save_run(old_running)

        # Recent completed run
        recent_run = Run(
            resource_id=resource.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        store.save_run(recent_run)

        count = store.purge_old_runs(days=90)
        assert count == 1

        assert store.get_run(old_run.id) is None
        assert store.get_run(old_running.id) is not None  # still running, not purged
        assert store.get_run(recent_run.id) is not None

    def test_purge_no_old_data(self, store):
        assert store.purge_old_reports(days=90) == 0
        assert store.purge_old_runs(days=90) == 0
