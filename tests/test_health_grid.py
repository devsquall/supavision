"""Tests for health grid (db.get_health_grid) and dashboard status banner logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from supavision.db import Store
from supavision.models import (
    Evaluation,
    Report,
    Resource,
    RunType,
    Severity,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temp database."""
    db_path = tmp_path / "test.db"
    s = Store(db_path)
    yield s
    s.close()


# ── Helper factories ────────────────────────────────────────────────


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


def _make_report(resource_id: str) -> Report:
    return Report(resource_id=resource_id, run_type=RunType.HEALTH_CHECK, content="Report content")


def _make_evaluation(
    report_id: str,
    resource_id: str,
    severity: Severity = Severity.HEALTHY,
    created_at: datetime | None = None,
) -> Evaluation:
    kwargs = {
        "report_id": report_id,
        "resource_id": resource_id,
        "severity": severity,
        "summary": f"Eval with severity {severity}",
        "should_alert": severity == Severity.CRITICAL,
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    return Evaluation(**kwargs)


# ── get_health_grid tests ───────────────────────────────────────────


class TestHealthGrid:
    def test_empty_when_no_evaluations(self, store):
        """No evaluations exist -> empty dict."""
        resource = _make_resource()
        store.save_resource(resource)

        grid = store.get_health_grid(resource.id, days=30)
        assert grid == {}

    def test_correct_date_grouping(self, store):
        """Evaluations on different days appear under their respective date keys."""
        resource = _make_resource()
        store.save_resource(resource)

        now = datetime.now(timezone.utc)
        day1 = now - timedelta(days=2)
        day2 = now - timedelta(days=1)

        report1 = _make_report(resource.id)
        store.save_report(report1)
        ev1 = _make_evaluation(report1.id, resource.id, Severity.HEALTHY, created_at=day1)
        store.save_evaluation(ev1)

        report2 = _make_report(resource.id)
        store.save_report(report2)
        ev2 = _make_evaluation(report2.id, resource.id, Severity.WARNING, created_at=day2)
        store.save_evaluation(ev2)

        grid = store.get_health_grid(resource.id, days=30)

        day1_str = day1.strftime("%Y-%m-%d")
        day2_str = day2.strftime("%Y-%m-%d")

        assert day1_str in grid
        assert day2_str in grid
        assert grid[day1_str] == ["healthy"]
        assert grid[day2_str] == ["warning"]

    def test_multiple_severities_same_day(self, store):
        """Multiple evaluations on the same day all appear in that day's list."""
        resource = _make_resource()
        store.save_resource(resource)

        now = datetime.now(timezone.utc)
        # Two evaluations a few hours apart on the same day
        t1 = now.replace(hour=8, minute=0, second=0, microsecond=0)
        t2 = now.replace(hour=14, minute=0, second=0, microsecond=0)

        report1 = _make_report(resource.id)
        store.save_report(report1)
        ev1 = _make_evaluation(report1.id, resource.id, Severity.HEALTHY, created_at=t1)
        store.save_evaluation(ev1)

        report2 = _make_report(resource.id)
        store.save_report(report2)
        ev2 = _make_evaluation(report2.id, resource.id, Severity.CRITICAL, created_at=t2)
        store.save_evaluation(ev2)

        grid = store.get_health_grid(resource.id, days=30)
        today_str = now.strftime("%Y-%m-%d")

        assert today_str in grid
        assert len(grid[today_str]) == 2
        assert "healthy" in grid[today_str]
        assert "critical" in grid[today_str]

    def test_only_includes_within_day_range(self, store):
        """Evaluations older than the specified day range are excluded."""
        resource = _make_resource()
        store.save_resource(resource)

        now = datetime.now(timezone.utc)
        # One evaluation 5 days ago (within 7-day range)
        recent = now - timedelta(days=5)
        # One evaluation 15 days ago (outside 7-day range)
        old = now - timedelta(days=15)

        report_recent = _make_report(resource.id)
        store.save_report(report_recent)
        ev_recent = _make_evaluation(
            report_recent.id, resource.id, Severity.HEALTHY, created_at=recent
        )
        store.save_evaluation(ev_recent)

        report_old = _make_report(resource.id)
        store.save_report(report_old)
        ev_old = _make_evaluation(
            report_old.id, resource.id, Severity.CRITICAL, created_at=old
        )
        store.save_evaluation(ev_old)

        grid = store.get_health_grid(resource.id, days=7)

        recent_str = recent.strftime("%Y-%m-%d")
        old_str = old.strftime("%Y-%m-%d")

        assert recent_str in grid
        assert old_str not in grid

    def test_excludes_evaluations_from_other_resources(self, store):
        """get_health_grid filters by resource_id — other resources' evals are excluded."""
        r1 = _make_resource(name="server-1")
        r2 = _make_resource(name="server-2")
        store.save_resource(r1)
        store.save_resource(r2)

        now = datetime.now(timezone.utc)

        # Evaluation for r1
        report1 = _make_report(r1.id)
        store.save_report(report1)
        ev1 = _make_evaluation(report1.id, r1.id, Severity.HEALTHY, created_at=now)
        store.save_evaluation(ev1)

        # Evaluation for r2
        report2 = _make_report(r2.id)
        store.save_report(report2)
        ev2 = _make_evaluation(report2.id, r2.id, Severity.CRITICAL, created_at=now)
        store.save_evaluation(ev2)

        grid_r1 = store.get_health_grid(r1.id, days=30)
        grid_r2 = store.get_health_grid(r2.id, days=30)

        today_str = now.strftime("%Y-%m-%d")

        # r1 grid should only have healthy
        assert grid_r1[today_str] == ["healthy"]
        # r2 grid should only have critical
        assert grid_r2[today_str] == ["critical"]


# ── Status banner logic tests ──────────────────────────────────────
#
# The banner logic from dashboard.py (dashboard_overview route):
#
#   if critical > 0 and critical == total:
#       status_text, status_type = "Major Outage", "critical"
#   elif critical > 0:
#       status_text, status_type = "Partial Outage", "critical"
#   elif warning > 0:
#       status_text, status_type = "Degraded Performance", "warning"
#   elif healthy > 0:
#       status_text, status_type = "All Systems Operational", "healthy"
#   else:
#       status_text, status_type = "Awaiting Data", "unknown"


def _compute_status_banner(
    critical: int, warning: int, healthy: int, total: int
) -> tuple[str, str]:
    """Extracted status banner logic from dashboard_overview route."""
    if critical > 0 and critical == total:
        return "Major Outage", "critical"
    elif critical > 0:
        return "Partial Outage", "critical"
    elif warning > 0:
        return "Degraded Performance", "warning"
    elif healthy > 0:
        return "All Systems Operational", "healthy"
    else:
        return "Awaiting Data", "unknown"


class TestStatusBanner:
    def test_all_critical_major_outage(self):
        """When every resource is critical -> Major Outage."""
        text, stype = _compute_status_banner(critical=3, warning=0, healthy=0, total=3)
        assert text == "Major Outage"
        assert stype == "critical"

    def test_some_critical_partial_outage(self):
        """When some (but not all) resources are critical -> Partial Outage."""
        text, stype = _compute_status_banner(critical=1, warning=1, healthy=1, total=3)
        assert text == "Partial Outage"
        assert stype == "critical"

    def test_only_warning_degraded(self):
        """When no critical but some warnings -> Degraded Performance."""
        text, stype = _compute_status_banner(critical=0, warning=2, healthy=1, total=3)
        assert text == "Degraded Performance"
        assert stype == "warning"

    def test_only_healthy_all_operational(self):
        """When everything is healthy -> All Systems Operational."""
        text, stype = _compute_status_banner(critical=0, warning=0, healthy=5, total=5)
        assert text == "All Systems Operational"
        assert stype == "healthy"

    def test_no_data_awaiting(self):
        """When no evaluations at all (no resources or none evaluated) -> Awaiting Data."""
        text, stype = _compute_status_banner(critical=0, warning=0, healthy=0, total=0)
        assert text == "Awaiting Data"
        assert stype == "unknown"

    def test_resources_exist_but_no_evaluations(self):
        """Resources exist but none have been evaluated yet.
        critical=0, warning=0, healthy=0, total=2 -> Awaiting Data."""
        text, stype = _compute_status_banner(critical=0, warning=0, healthy=0, total=2)
        assert text == "Awaiting Data"
        assert stype == "unknown"

    def test_single_critical_is_major_outage(self):
        """Edge case: only one resource, and it is critical -> Major Outage (critical == total)."""
        text, stype = _compute_status_banner(critical=1, warning=0, healthy=0, total=1)
        assert text == "Major Outage"
        assert stype == "critical"

    def test_warning_with_healthy_still_degraded(self):
        """Mix of warning and healthy (no critical) -> Degraded Performance."""
        text, stype = _compute_status_banner(critical=0, warning=1, healthy=4, total=5)
        assert text == "Degraded Performance"
        assert stype == "warning"
