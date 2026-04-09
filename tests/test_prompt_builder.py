"""Tests for implementation prompt generation."""

import pytest

from supavision.models import (
    Finding,
    FindingSeverity,
    ManualTask,
    Priority,
    TaskCategory,
)
from supavision.prompt_builder import (
    generate_implementation_prompt,
    generate_task_prompt,
    generate_work_item_prompt,
)


@pytest.fixture
def finding():
    return Finding(
        resource_id="r1",
        category="sql-injection",
        severity=FindingSeverity.HIGH,
        language="python",
        file_path="src/db.py",
        line_number=42,
        snippet='cursor.execute(f"SELECT * FROM {uid}")',
        context_before=["def get_user(uid):", "    conn = get_conn()"],
        context_after=["    return cursor.fetchone()"],
        evaluation_reasoning="User input directly interpolated.",
        evaluation_fix_approach="Use parameterized queries.",
        evaluation_effort="small",
    )


@pytest.fixture
def manual_task():
    return ManualTask(
        resource_id="r1",
        title="Add rate limiting",
        description="Add rate limiting to /api/users.",
        task_category=TaskCategory.FEATURE,
        priority=Priority.HIGH,
        evaluation_reasoning="Straightforward middleware addition.",
        evaluation_fix_approach="Add rate limiter middleware.",
        evaluation_effort="medium",
    )


class TestImplementationPrompt:
    def test_contains_code_context(self, finding):
        prompt = generate_implementation_prompt(finding)
        assert "src/db.py:42" in prompt
        assert "FIX THIS" in prompt
        assert "sql-injection" in prompt

    def test_contains_evaluation(self, finding):
        prompt = generate_implementation_prompt(finding)
        assert "User input directly interpolated" in prompt
        assert "parameterized queries" in prompt

    def test_contains_effort(self, finding):
        prompt = generate_implementation_prompt(finding)
        assert "small" in prompt

    def test_resource_name(self, finding):
        prompt = generate_implementation_prompt(finding, resource_name="MyProject")
        assert "MyProject" in prompt


class TestTaskPrompt:
    def test_contains_task_info(self, manual_task):
        prompt = generate_task_prompt(manual_task)
        assert "Add rate limiting" in prompt
        assert "/api/users" in prompt

    def test_contains_evaluation(self, manual_task):
        prompt = generate_task_prompt(manual_task)
        assert "middleware" in prompt


class TestDispatcher:
    def test_routes_finding(self, finding):
        prompt = generate_work_item_prompt(finding)
        assert "FIX THIS" in prompt

    def test_routes_task(self, manual_task):
        prompt = generate_work_item_prompt(manual_task)
        assert "Add rate limiting" in prompt
