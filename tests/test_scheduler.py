"""Tests for Scheduler — dispatch, due-job logic, and stale run recovery.

These tests use a real Store (SQLite in tmp_path) and a mock Engine so that
scheduling logic is exercised against real DB state without needing Claude CLI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from supavision.db import Store
from supavision.engine import Engine
from supavision.models import Resource, Run, RunStatus, RunType
from supavision.models.core import Schedule
from supavision.scheduler import Scheduler

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "scheduler_test.db")
    yield s
    s.close()


@pytest.fixture
def engine():
    e = MagicMock(spec=Engine)
    e.run_discovery = MagicMock()
    e.run_health_check = MagicMock()
    e.run_discovery_async = AsyncMock()
    e.run_health_check_async = AsyncMock()
    return e


@pytest.fixture
def scheduler(store, engine):
    return Scheduler(store, engine)


def _resource(**kwargs) -> Resource:
    return Resource(name="test-server", resource_type="server", **kwargs)


def _completed_run(resource_id: str, run_type: RunType, completed_ago: timedelta) -> Run:
    """Return a COMPLETED Run with completed_at set to `completed_ago` in the past."""
    run = Run(resource_id=resource_id, run_type=run_type, status=RunStatus.COMPLETED)
    run.started_at = datetime.now(timezone.utc) - completed_ago - timedelta(minutes=1)
    run.completed_at = datetime.now(timezone.utc) - completed_ago
    return run


def _running_run(resource_id: str, run_type: RunType, started_ago: timedelta) -> Run:
    """Return a RUNNING Run with started_at set to `started_ago` in the past, no completed_at."""
    run = Run(resource_id=resource_id, run_type=run_type, status=RunStatus.RUNNING)
    run.started_at = datetime.now(timezone.utc) - started_ago
    return run


# ── TestExecuteRunDispatch ───────────────────────────────────────────────


class TestExecuteRunDispatch:
    """Regression guard for the dispatch bug: each RunType must call ONLY
    its own engine method and never the other."""

    def test_discovery_calls_run_discovery_only(self, scheduler, engine, store):
        resource = _resource()
        store.save_resource(resource)
        scheduler._execute_run(resource, RunType.DISCOVERY)
        engine.run_discovery.assert_called_once_with(resource.id)
        engine.run_health_check.assert_not_called()

    def test_health_check_calls_run_health_check_only(self, scheduler, engine, store):
        resource = _resource()
        store.save_resource(resource)
        scheduler._execute_run(resource, RunType.HEALTH_CHECK)
        engine.run_health_check.assert_called_once_with(resource.id)
        engine.run_discovery.assert_not_called()

    async def test_async_discovery_routes_correctly(self, scheduler, engine, store):
        resource = _resource()
        store.save_resource(resource)
        await scheduler._execute_run_async(resource, RunType.DISCOVERY)
        engine.run_discovery_async.assert_called_once_with(resource.id)
        engine.run_health_check_async.assert_not_called()

    async def test_async_health_check_routes_correctly(self, scheduler, engine, store):
        resource = _resource()
        store.save_resource(resource)
        await scheduler._execute_run_async(resource, RunType.HEALTH_CHECK)
        engine.run_health_check_async.assert_called_once_with(resource.id)
        engine.run_discovery_async.assert_not_called()

    def test_engine_none_does_not_raise(self, store):
        """Scheduler with no engine should log a warning and return, not raise."""
        sched = Scheduler(store, None)
        resource = _resource()
        store.save_resource(resource)
        sched._execute_run(resource, RunType.HEALTH_CHECK)  # must not raise

    async def test_async_engine_none_does_not_raise(self, store):
        sched = Scheduler(store, None)
        resource = _resource()
        store.save_resource(resource)
        await sched._execute_run_async(resource, RunType.HEALTH_CHECK)  # must not raise


# ── TestGetDueJobs ───────────────────────────────────────────────────────


class TestGetDueJobs:
    """Scheduling logic: what makes a resource due?"""

    def test_first_run_resource_is_immediately_due(self, scheduler, store):
        resource = _resource(
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True)
        )
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        ids = [r.id for r, _ in due]
        assert resource.id in ids

    def test_first_run_correct_run_type_returned(self, scheduler, store):
        resource = _resource(
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True)
        )
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        entry = next((t for t in due if t[0].id == resource.id), None)
        assert entry is not None
        assert entry[1] == RunType.HEALTH_CHECK

    def test_both_schedule_types_returned_for_first_run(self, scheduler, store):
        resource = _resource(
            discovery_schedule=Schedule(cron="0 0 * * *", enabled=True),
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True),
        )
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        types = {t for r, t in due if r.id == resource.id}
        assert RunType.DISCOVERY in types
        assert RunType.HEALTH_CHECK in types

    def test_resource_with_recent_completed_run_not_due_even_if_also_running(self, scheduler, store):
        """get_latest_runs_batch only returns COMPLETED runs. The scheduler uses the
        last completed run's timestamp for cron evaluation; a RUNNING run with a recent
        completed predecessor is not re-scheduled (the engine lock is the final guard
        against actual double-dispatch)."""
        resource = _resource(
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True)  # hourly
        )
        store.save_resource(resource)
        # Last completed run 5 minutes ago — next hourly slot is ~55 min away
        completed = _completed_run(resource.id, RunType.HEALTH_CHECK, completed_ago=timedelta(minutes=5))
        store.save_run(completed)
        # A RUNNING run also exists (e.g. triggered manually)
        running = _running_run(resource.id, RunType.HEALTH_CHECK, started_ago=timedelta(minutes=2))
        store.save_run(running)
        due = scheduler._get_due_jobs()
        ids = [r.id for r, _ in due]
        assert resource.id not in ids

    def test_resource_due_after_interval_elapsed(self, scheduler, store):
        resource = _resource(
            health_check_schedule=Schedule(cron="*/5 * * * *", enabled=True)
        )
        store.save_resource(resource)
        run = _completed_run(resource.id, RunType.HEALTH_CHECK, completed_ago=timedelta(hours=1))
        store.save_run(run)
        due = scheduler._get_due_jobs()
        ids = [r.id for r, _ in due]
        assert resource.id in ids

    def test_resource_not_due_before_interval(self, scheduler, store):
        resource = _resource(
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True)  # hourly
        )
        store.save_resource(resource)
        # Completed 5 minutes ago — next run ~55 min away
        run = _completed_run(resource.id, RunType.HEALTH_CHECK, completed_ago=timedelta(minutes=5))
        store.save_run(run)
        due = scheduler._get_due_jobs()
        ids = [r.id for r, _ in due]
        assert resource.id not in ids

    def test_disabled_resource_is_skipped(self, scheduler, store):
        resource = _resource(
            enabled=False,
            health_check_schedule=Schedule(cron="0 * * * *", enabled=True),
        )
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        assert not any(r.id == resource.id for r, _ in due)

    def test_disabled_schedule_is_skipped(self, scheduler, store):
        resource = _resource(
            health_check_schedule=Schedule(cron="0 * * * *", enabled=False)
        )
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        assert not any(r.id == resource.id for r, _ in due)

    def test_resource_without_schedule_is_skipped(self, scheduler, store):
        resource = _resource()  # no schedules set
        store.save_resource(resource)
        due = scheduler._get_due_jobs()
        assert not any(r.id == resource.id for r, _ in due)


# ── TestRecoverStaleRuns ─────────────────────────────────────────────────


class TestRecoverStaleRuns:
    """Stale run recovery: RUNNING runs older than 4h should be marked FAILED."""

    def test_stale_run_is_marked_failed(self, scheduler, store):
        resource = _resource()
        store.save_resource(resource)
        run = _running_run(resource.id, RunType.HEALTH_CHECK, started_ago=timedelta(hours=5))
        store.save_run(run)

        scheduler._recover_stale_runs()

        recovered = store.get_run(run.id)
        assert str(recovered.status) == "failed"
        assert recovered.completed_at is not None
        assert "Recovered" in (recovered.error or "")

    def test_recent_running_run_is_not_touched(self, scheduler, store):
        resource = _resource()
        store.save_resource(resource)
        run = _running_run(resource.id, RunType.HEALTH_CHECK, started_ago=timedelta(hours=1))
        store.save_run(run)

        scheduler._recover_stale_runs()

        untouched = store.get_run(run.id)
        assert str(untouched.status) == "running"
        assert untouched.completed_at is None

    def test_already_completed_run_is_not_touched(self, scheduler, store):
        resource = _resource()
        store.save_resource(resource)
        run = _completed_run(resource.id, RunType.HEALTH_CHECK, completed_ago=timedelta(hours=10))
        store.save_run(run)

        scheduler._recover_stale_runs()

        untouched = store.get_run(run.id)
        assert str(untouched.status) == "completed"
