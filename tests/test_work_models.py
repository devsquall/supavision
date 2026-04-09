"""Tests for Lane 2 models and lifecycle transitions."""

import pytest

from supavision.models import (
    VALID_TRANSITIONS,
    Finding,
    FindingSeverity,
    FindingStage,
    ManualTask,
    Priority,
    TaskCategory,
    TaskSource,
)


@pytest.fixture
def sample_finding():
    return Finding(
        id="find1",
        resource_id="res1",
        stage=FindingStage.SCANNED,
        category="sql-injection",
        severity=FindingSeverity.HIGH,
        language="python",
        file_path="src/db.py",
        line_number=42,
        snippet='.execute(f"SELECT * FROM users WHERE id={user_id}")',
        context_before=["def get_user(user_id):", "    conn = get_conn()"],
        context_after=["    return cursor.fetchone()", ""],
        pattern_name="SQL query with f-string interpolation",
        run_id="run1",
    )


@pytest.fixture
def sample_manual_task():
    return ManualTask(
        id="task1",
        resource_id="res1",
        title="Add rate limiting to API",
        description="The /api/users endpoint needs rate limiting.",
        task_category=TaskCategory.FEATURE,
        priority=Priority.HIGH,
    )


class TestManualTask:
    def test_created_to_evaluated(self, sample_manual_task):
        sample_manual_task.transition_to(FindingStage.EVALUATED)
        assert sample_manual_task.stage == FindingStage.EVALUATED

    def test_created_to_dismissed(self, sample_manual_task):
        sample_manual_task.transition_to(FindingStage.DISMISSED)
        assert sample_manual_task.stage == FindingStage.DISMISSED

    def test_created_skip_to_approved_invalid(self, sample_manual_task):
        """Approved is a legacy stage — no longer reachable."""
        with pytest.raises(ValueError):
            sample_manual_task.transition_to(FindingStage.APPROVED)

    def test_lifecycle_evaluate_then_dismiss(self, sample_manual_task):
        sample_manual_task.transition_to(FindingStage.EVALUATED)
        sample_manual_task.transition_to(FindingStage.DISMISSED)
        assert sample_manual_task.stage == FindingStage.DISMISSED

    def test_display_title(self, sample_manual_task):
        assert sample_manual_task.display_title == "Add rate limiting to API"

    def test_dedup_signature_unique(self, sample_manual_task):
        sig = sample_manual_task.dedup_signature
        assert sig == (sample_manual_task.id, sample_manual_task.id)

    def test_source_is_manual(self, sample_manual_task):
        assert sample_manual_task.source == TaskSource.MANUAL

    def test_default_stage_is_created(self):
        task = ManualTask(resource_id="r1", title="Test")
        assert task.stage == FindingStage.CREATED


class TestFinding:
    def test_source_is_scanner(self, sample_finding):
        assert sample_finding.source == TaskSource.SCANNER

    def test_display_title(self, sample_finding):
        assert "sql-injection" in sample_finding.display_title
        assert "src/db.py" in sample_finding.display_title

    def test_scanned_to_evaluated(self, sample_finding):
        sample_finding.transition_to(FindingStage.EVALUATED)
        assert sample_finding.stage == FindingStage.EVALUATED

    def test_scanned_to_dismissed(self, sample_finding):
        sample_finding.transition_to(FindingStage.DISMISSED)
        assert sample_finding.stage == FindingStage.DISMISSED

    def test_invalid_transition_to_approved(self, sample_finding):
        """Approved is a legacy stage — not reachable from scanned."""
        with pytest.raises(ValueError, match="Invalid transition"):
            sample_finding.transition_to(FindingStage.APPROVED)

    def test_dismissed_is_terminal(self, sample_finding):
        sample_finding.transition_to(FindingStage.DISMISSED)
        with pytest.raises(ValueError):
            sample_finding.transition_to(FindingStage.EVALUATED)

    def test_full_insight_lifecycle(self, sample_finding):
        """Scan → evaluate → dismiss (the insight lifecycle)."""
        sample_finding.transition_to(FindingStage.EVALUATED)
        sample_finding.transition_to(FindingStage.DISMISSED)
        assert sample_finding.stage == FindingStage.DISMISSED

    def test_confidence_field(self, sample_finding):
        assert sample_finding.confidence == 0.0
        sample_finding.confidence = 0.92
        assert sample_finding.confidence == 0.92

    def test_dedup_signature(self, sample_finding):
        sig = sample_finding.dedup_signature
        assert sig == ("src/db.py", "sql-injection")

    def test_all_transitions_have_entries(self):
        for stage in FindingStage:
            assert stage in VALID_TRANSITIONS
