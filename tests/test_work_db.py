"""Tests for Lane 2 database CRUD operations."""

import pytest

from supavision.db import Store
from supavision.models import (
    AgentJob,
    BlocklistEntry,
    Feedback,
    FeedbackType,
    Finding,
    FindingSeverity,
    FindingStage,
    JobStatus,
    ManualTask,
    Priority,
    TaskCategory,
)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def finding():
    return Finding(
        id="f1",
        resource_id="r1",
        category="sql-injection",
        severity=FindingSeverity.HIGH,
        language="python",
        file_path="src/db.py",
        line_number=42,
        snippet='execute(f"SELECT...")',
        run_id="run1",
    )


@pytest.fixture
def manual_task():
    return ManualTask(
        id="t1",
        resource_id="r1",
        title="Fix auth bug",
        description="The login flow is broken.",
        task_category=TaskCategory.BUG,
        priority=Priority.HIGH,
    )


class TestWorkItemCRUD:
    def test_save_and_get_finding(self, store, finding):
        store.save_work_item(finding)
        got = store.get_work_item("f1")
        assert got is not None
        assert isinstance(got, Finding)
        assert got.category == "sql-injection"
        assert got.resource_id == "r1"

    def test_save_and_get_manual_task(self, store, manual_task):
        store.save_work_item(manual_task)
        got = store.get_work_item("t1")
        assert got is not None
        assert isinstance(got, ManualTask)
        assert got.title == "Fix auth bug"

    def test_list_work_items_filter_by_resource(self, store, finding, manual_task):
        store.save_work_item(finding)
        store.save_work_item(manual_task)
        items, total = store.list_work_items(resource_id="r1")
        assert total == 2

    def test_list_work_items_filter_by_stage(self, store, finding, manual_task):
        store.save_work_item(finding)
        store.save_work_item(manual_task)
        items, total = store.list_work_items(stage="scanned")
        assert total == 1
        assert items[0].id == "f1"

    def test_list_work_items_filter_by_source(self, store, finding, manual_task):
        store.save_work_item(finding)
        store.save_work_item(manual_task)
        items, total = store.list_work_items(source="manual")
        assert total == 1
        assert items[0].id == "t1"

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_work_item("nonexistent") is None

    def test_transition_work_item(self, store, finding):
        store.save_work_item(finding)
        updated = store.transition_work_item("f1", FindingStage.EVALUATED)
        assert updated.stage == FindingStage.EVALUATED
        # Verify persisted
        got = store.get_work_item("f1")
        assert got.stage == FindingStage.EVALUATED

    def test_transition_invalid_raises(self, store, finding):
        store.save_work_item(finding)
        with pytest.raises(ValueError, match="Invalid transition"):
            store.transition_work_item("f1", FindingStage.COMPLETED)

    def test_transition_records_audit_trail(self, store, finding):
        store.save_work_item(finding)
        store.transition_work_item("f1", FindingStage.EVALUATED)
        transitions = store.list_transitions("f1")
        assert len(transitions) == 1
        assert transitions[0].from_stage == "scanned"
        assert transitions[0].to_stage == "evaluated"

    def test_delete_work_item_cascades(self, store, finding):
        store.save_work_item(finding)
        store.save_feedback(Feedback(work_item_id="f1", feedback_type=FeedbackType.FALSE_POSITIVE))
        store.save_agent_job(AgentJob(work_item_id="f1", resource_id="r1", job_type="evaluate"))
        store.transition_work_item("f1", FindingStage.EVALUATED)
        assert store.delete_work_item("f1")
        assert store.get_work_item("f1") is None
        assert len(store.list_feedback("f1")) == 0
        assert len(store.list_transitions("f1")) == 0

    def test_work_item_exists(self, store, finding):
        store.save_work_item(finding)
        assert store.work_item_exists("r1", "src/db.py", "sql-injection")
        assert not store.work_item_exists("r1", "src/db.py", "xss")

    def test_count_by_stage(self, store, finding, manual_task):
        store.save_work_item(finding)
        store.save_work_item(manual_task)
        counts = store.count_work_items_by_stage(resource_id="r1")
        assert counts.get("scanned") == 1
        assert counts.get("created") == 1

    def test_pagination(self, store):
        for i in range(15):
            store.save_work_item(Finding(
                id=f"f{i}", resource_id="r1", category="test",
                severity=FindingSeverity.LOW, language="python",
                file_path=f"file{i}.py", line_number=1, snippet="test",
            ))
        items, total = store.list_work_items(resource_id="r1", page=1, per_page=10)
        assert total == 15
        assert len(items) == 10
        items2, total2 = store.list_work_items(resource_id="r1", page=2, per_page=10)
        assert total2 == 15
        assert len(items2) == 5


class TestAgentJobCRUD:
    def test_save_and_get(self, store):
        job = AgentJob(id="j1", work_item_id="f1", resource_id="r1", job_type="evaluate")
        store.save_agent_job(job)
        got = store.get_agent_job("j1")
        assert got is not None
        assert got.job_type == "evaluate"

    def test_list_pending(self, store):
        store.save_agent_job(AgentJob(
            id="j1", work_item_id="f1", resource_id="r1", job_type="evaluate",
        ))
        store.save_agent_job(AgentJob(
            id="j2", work_item_id="f2", resource_id="r1",
            job_type="implement", status=JobStatus.RUNNING,
        ))
        pending = store.get_pending_agent_jobs()
        assert len(pending) == 1
        assert pending[0].id == "j1"

    def test_list_by_resource(self, store):
        store.save_agent_job(AgentJob(id="j1", work_item_id="f1", resource_id="r1", job_type="evaluate"))
        store.save_agent_job(AgentJob(id="j2", work_item_id="f2", resource_id="r2", job_type="evaluate"))
        jobs = store.list_agent_jobs(resource_id="r1")
        assert len(jobs) == 1


class TestBlocklistCRUD:
    def test_save_and_get(self, store):
        entry = BlocklistEntry(
            id="b1", pattern_signature="sql:python:abc123",
            category="sql-injection", language="python", description="Test",
        )
        store.save_blocklist_entry(entry)
        got = store.get_blocklist_entry_by_signature("sql:python:abc123")
        assert got is not None
        assert got.category == "sql-injection"

    def test_list_blocklist(self, store):
        store.save_blocklist_entry(BlocklistEntry(
            id="b1", pattern_signature="sql:py:abc",
            category="sql-injection", language="python", description="Test1",
        ))
        store.save_blocklist_entry(BlocklistEntry(
            id="b2", pattern_signature="xss:js:def",
            category="xss", language="js", description="Test2",
        ))
        all_entries = store.list_blocklist()
        assert len(all_entries) == 2
        sql_only = store.list_blocklist(category="sql-injection")
        assert len(sql_only) == 1

    def test_delete_blocklist(self, store):
        store.save_blocklist_entry(BlocklistEntry(
            id="b1", pattern_signature="test:sig",
            category="test", language="any", description="Test",
        ))
        assert store.delete_blocklist_entry("b1")
        assert store.get_blocklist_entry_by_signature("test:sig") is None


class TestFeedbackCRUD:
    def test_save_and_list(self, store):
        store.save_feedback(Feedback(
            id="fb1", work_item_id="f1",
            feedback_type=FeedbackType.FALSE_POSITIVE, reason="Not real",
        ))
        store.save_feedback(Feedback(
            id="fb2", work_item_id="f1",
            feedback_type=FeedbackType.BY_DESIGN, reason="Intentional",
        ))
        fbs = store.list_feedback("f1")
        assert len(fbs) == 2
