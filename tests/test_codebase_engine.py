"""Tests for the CodebaseEngine."""

import pytest

from supavision.codebase_engine import CodebaseEngine
from supavision.db import Store
from supavision.models import (
    FindingStage,
    Resource,
    RunStatus,
    RunType,
)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def vuln_dir(tmp_path):
    """Create a temp directory with vulnerable files."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "app.py").write_text("result = eval(user_input)\n")
    (project_dir / "db.py").write_text(
        'cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n'
    )
    return str(project_dir)


@pytest.fixture
def codebase_resource(store, vuln_dir):
    resource = Resource(
        id="cb-1",
        name="Test Codebase",
        resource_type="codebase",
        config={"path": vuln_dir},
    )
    store.save_resource(resource)
    return resource


@pytest.fixture
def engine(store):
    return CodebaseEngine(store)


class TestRunScan:
    def test_scan_creates_run(self, engine, codebase_resource):
        run = engine.run_scan("cb-1")
        assert run.status == RunStatus.COMPLETED
        assert run.run_type == RunType.SCAN
        assert run.report_id is not None

    def test_scan_creates_findings(self, engine, store, codebase_resource):
        engine.run_scan("cb-1")
        items, total = store.list_work_items(resource_id="cb-1")
        assert total >= 2
        assert all(item.stage == FindingStage.SCANNED for item in items)
        categories = {item.category for item in items}
        assert "code-injection" in categories or "sql-injection" in categories

    def test_scan_creates_report(self, engine, store, codebase_resource):
        run = engine.run_scan("cb-1")
        report = store.get_report(run.report_id)
        assert report is not None
        assert "finding" in report.content.lower()

    def test_scan_creates_one_evaluation(self, engine, store, codebase_resource):
        """Scan creates exactly one resource-level evaluation (Lane 1 health badge)."""
        engine.run_scan("cb-1")
        evals = store.get_recent_evaluations("cb-1")
        assert len(evals) == 1
        # Should be warning or critical (we have vulnerable code)
        assert evals[0].severity.value in ("warning", "critical")

    def test_scan_wrong_resource_type_raises(self, engine, store):
        resource = Resource(
            id="srv-1", name="Server", resource_type="server",
            config={"ssh_host": "example.com"},
        )
        store.save_resource(resource)
        with pytest.raises(ValueError, match="not 'codebase'"):
            engine.run_scan("srv-1")

    def test_scan_missing_resource_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.run_scan("nonexistent")

    def test_scan_bad_path_raises(self, engine, store):
        resource = Resource(
            id="cb-bad", name="Bad Path", resource_type="codebase",
            config={"path": "/nonexistent/path"},
        )
        store.save_resource(resource)
        with pytest.raises(ValueError, match="not found"):
            engine.run_scan("cb-bad")

    def test_incremental_scan_dedups(self, engine, store, codebase_resource):
        """Second scan should not create duplicate findings."""
        engine.run_scan("cb-1")
        items1, total1 = store.list_work_items(resource_id="cb-1")

        engine.run_scan("cb-1")
        items2, total2 = store.list_work_items(resource_id="cb-1")

        # Should not have doubled
        assert total2 == total1


class TestCreateJobs:
    def test_create_evaluate_job(self, engine, store, codebase_resource):
        engine.run_scan("cb-1")
        items, _ = store.list_work_items(resource_id="cb-1")
        item = items[0]

        job = engine.create_evaluate_job(item.id, "cb-1")
        assert job.job_type == "evaluate"
        assert job.work_item_id == item.id
        assert job.status.value == "pending"

    def test_insight_lifecycle(
        self, engine, store, codebase_resource
    ):
        """Findings follow the insight lifecycle: scan -> evaluate -> dismiss."""
        engine.run_scan("cb-1")
        items, _ = store.list_work_items(resource_id="cb-1")
        item = items[0]
        assert item.stage == FindingStage.SCANNED

        store.transition_work_item(item.id, FindingStage.EVALUATED)
        item = store.get_work_item(item.id)
        assert item.stage == FindingStage.EVALUATED

        store.transition_work_item(item.id, FindingStage.DISMISSED)
        item = store.get_work_item(item.id)
        assert item.stage == FindingStage.DISMISSED

    def test_create_scout_job(self, engine, store, codebase_resource):
        job = engine.create_scout_job("cb-1", "security")
        assert job.job_type == "scout-security"
        assert job.resource_id == "cb-1"

    def test_create_job_missing_item_raises(self, engine, store, codebase_resource):
        with pytest.raises(ValueError, match="not found"):
            engine.create_evaluate_job("nonexistent", "cb-1")
