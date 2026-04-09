"""Integration test: two-lane boundary verification.

Creates a codebase resource, runs a scan, and verifies that:
- A Report is created with aggregate summary (Lane 1)
- N WorkItems are created with stage=SCANNED (Lane 2)
- The Report has no lifecycle stage fields
- The WorkItems have no resource-health severity
- No rows are added to the evaluations table
"""

import pytest

from supavision.db import Store
from supavision.models import (
    FindingSeverity,
    FindingStage,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
)
from supavision.scanner import scan_directory


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def codebase_resource():
    return Resource(
        id="codebase-1",
        name="Test Codebase",
        resource_type="codebase",
        config={"path": "/tmp/test-project"},
    )


@pytest.fixture
def vuln_project(tmp_path):
    """Create a temp directory with some vulnerable files."""
    (tmp_path / "app.py").write_text(
        'import os\n'
        'result = eval(user_input)\n'
        'os.system(f"rm {filename}")\n'
    )
    (tmp_path / "db.py").write_text(
        'cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n'
    )
    return tmp_path


class TestTwoLaneBoundary:
    def test_scan_produces_work_items_not_evaluations(self, store, codebase_resource, vuln_project):
        """The core boundary test: scan creates WorkItems (Lane 2) and a Report (Lane 1),
        but never writes to the evaluations table."""
        store.save_resource(codebase_resource)

        # Create a run
        run = Run(
            id="run-1",
            resource_id=codebase_resource.id,
            run_type=RunType.SCAN,
            status=RunStatus.RUNNING,
        )
        store.save_run(run)

        # Run scanner (Lane 2 operation)
        result, findings = scan_directory(
            resource_id=codebase_resource.id,
            directory=str(vuln_project),
            run_id=run.id,
        )

        # Save findings as WorkItems (Lane 2)
        for f in findings:
            store.save_work_item(f)

        # Create aggregate Report (Lane 1)
        report = Report(
            id="report-1",
            resource_id=codebase_resource.id,
            run_type=RunType.SCAN,
            content=f"Scan completed: {result.summary}",
        )
        store.save_report(report)

        # Complete the run
        run.status = RunStatus.COMPLETED
        run.report_id = report.id
        store.save_run(run)

        # ── Assertions ──────────────────────────────────────────────

        # (a) Report was created with aggregate summary
        saved_report = store.get_report("report-1")
        assert saved_report is not None
        assert "finding" in saved_report.content.lower() or "scan" in saved_report.content.lower()

        # (b) N WorkItems were created with stage=SCANNED
        items, total = store.list_work_items(resource_id=codebase_resource.id)
        assert total >= 2, f"Expected at least 2 findings, got {total}"
        assert all(item.stage == FindingStage.SCANNED for item in items)

        # (c) The Report has no lifecycle stage fields
        report_dict = saved_report.model_dump()
        assert "stage" not in report_dict
        assert "evaluation_verdict" not in report_dict
        assert "evaluation_reasoning" not in report_dict

        # (d) WorkItems have FindingSeverity, not resource-health Severity
        for item in items:
            assert isinstance(item.severity, FindingSeverity)
            assert item.severity.value in ("critical", "high", "medium", "low", "info")
            # Ensure it's NOT resource-health severity
            assert item.severity.value not in ("healthy", "warning")

        # (e) The raw scanner does NOT create evaluations — only CodebaseEngine does
        # (CodebaseEngine adds one aggregate resource-level evaluation for the health badge)
        evals = store.get_recent_evaluations(codebase_resource.id)
        assert len(evals) == 0, (
            f"Raw scanner should not create evaluations. Got {len(evals)}. "
            f"Only CodebaseEngine creates the resource-level health evaluation."
        )

    def test_work_items_link_to_run(self, store, codebase_resource, vuln_project):
        """WorkItems should reference the run_id that created them."""
        store.save_resource(codebase_resource)

        result, findings = scan_directory(
            resource_id=codebase_resource.id,
            directory=str(vuln_project),
            run_id="run-42",
        )

        for f in findings:
            store.save_work_item(f)

        items, _ = store.list_work_items(resource_id=codebase_resource.id, run_id="run-42")
        assert len(items) == len(findings)
        assert all(item.run_id == "run-42" for item in items)

    def test_resource_delete_cascades_both_lanes(self, store, codebase_resource, vuln_project):
        """Deleting a resource cleans up both Lane 1 and Lane 2 data."""
        store.save_resource(codebase_resource)

        # Create Lane 1 data
        report = Report(
            id="r1", resource_id=codebase_resource.id,
            run_type=RunType.SCAN, content="test",
        )
        store.save_report(report)

        # Create Lane 2 data
        result, findings = scan_directory(
            resource_id=codebase_resource.id,
            directory=str(vuln_project),
        )
        for f in findings:
            store.save_work_item(f)

        # Delete resource
        store.delete_resource(codebase_resource.id)

        # Verify both lanes are cleaned up
        assert store.get_resource(codebase_resource.id) is None
        assert store.get_report("r1") is None
        items, total = store.list_work_items(resource_id=codebase_resource.id)
        assert total == 0
