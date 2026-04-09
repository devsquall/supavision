"""Tests for code_evaluator prompt generation and result parsing."""

import pytest

from supavision.code_evaluator import (
    generate_eval_prompt,
    generate_task_eval_prompt,
    generate_work_item_eval_prompt,
    parse_eval_result,
)
from supavision.models import (
    BlocklistEntry,
    Finding,
    FindingSeverity,
    ManualTask,
    Priority,
    TaskCategory,
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
        pattern_name="SQL f-string interpolation",
    )


@pytest.fixture
def manual_task():
    return ManualTask(
        resource_id="r1",
        title="Add rate limiting",
        description="API needs rate limiting.",
        task_category=TaskCategory.FEATURE,
        priority=Priority.HIGH,
    )


class TestEvalPrompt:
    def test_finding_prompt_contains_code(self, finding):
        prompt = generate_eval_prompt(finding)
        assert "sql-injection" in prompt
        assert "src/db.py:42" in prompt
        assert "SELECT * FROM" in prompt
        assert "Three Killer Questions" in prompt

    def test_finding_prompt_with_blocklist(self, finding):
        entries = [
            BlocklistEntry(
                pattern_signature="sig1",
                category="sql-injection",
                language="python",
                description="Known safe ORM pattern",
            ),
            BlocklistEntry(
                pattern_signature="sig2",
                category="xss",
                language="js",
                description="Unrelated entry",
            ),
        ]
        prompt = generate_eval_prompt(finding, entries)
        assert "Known safe ORM pattern" in prompt
        assert "Unrelated entry" not in prompt

    def test_task_prompt(self, manual_task):
        prompt = generate_task_eval_prompt(manual_task)
        assert "Add rate limiting" in prompt
        assert "feature" in prompt
        assert "feasible | needs_clarification" in prompt

    def test_dispatcher_routes_finding(self, finding):
        prompt = generate_work_item_eval_prompt(finding)
        assert "Three Killer Questions" in prompt

    def test_dispatcher_routes_task(self, manual_task):
        prompt = generate_work_item_eval_prompt(manual_task)
        assert "feasible | needs_clarification" in prompt


class TestParseEvalResult:
    def test_parse_json_in_code_block(self):
        text = '```json\n{"verdict": "true_positive", "reasoning": "Exploitable via user input."}\n```'
        result = parse_eval_result(text)
        assert result["verdict"] == "true_positive"
        assert "Exploitable" in result["reasoning"]

    def test_parse_raw_json(self):
        text = '{"verdict": "false_positive", "reasoning": "ORM handles escaping.", "effort": "trivial"}'
        result = parse_eval_result(text)
        assert result["verdict"] == "false_positive"
        assert result["effort"] == "trivial"

    def test_parse_natural_language_false_positive(self):
        text = "This is a false positive because the input is always sanitized upstream."
        result = parse_eval_result(text)
        assert result["verdict"] == "false_positive"
        assert "sanitized" in result["reasoning"]

    def test_parse_natural_language_true_positive(self):
        text = "This is a true positive. The input is user-controlled and directly interpolated."
        result = parse_eval_result(text)
        assert result["verdict"] == "true_positive"

    def test_parse_empty_returns_empty(self):
        result = parse_eval_result("")
        assert result.get("reasoning") == ""
