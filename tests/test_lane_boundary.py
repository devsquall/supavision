"""Lane boundary enforcement tests.

Verifies that infrastructure domain files (Lane 1) do not import Lane 2 models,
and codebase domain files (Lane 2) do not import Lane 1 models or write to
the evaluations table.

Uses AST parsing — no runtime side effects.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "supavision"

# Infrastructure domain (Lane 1): must NOT import from models.work
INFRA_FILES = [
    "engine.py",
    "executor.py",
    "tools.py",
    "evaluator.py",
    "discovery_diff.py",
]

# Codebase domain (Lane 2): must NOT import Evaluation/SystemContext or call save_evaluation
CODEBASE_FILES = [
    "scanner.py",
    "blocklist.py",
    "code_evaluator.py",
    "prompt_builder.py",
    "agent_runner.py",
]

# Lane 2 symbols that infra files must not import
LANE2_SYMBOLS = {
    "FindingStage", "FindingSeverity", "Finding", "ManualTask", "WorkItem",
    "AgentJob", "Feedback", "Transition", "BlocklistEntry",
    "FeedbackType", "TaskSource", "JobStatus", "TaskCategory", "Priority",
    "VALID_TRANSITIONS",
}

# Lane 1 symbols that codebase files must not import
LANE1_SYMBOLS = {
    "Evaluation", "SystemContext", "Checklist", "ChecklistItem", "Severity",
}


def _get_imports(filepath: Path) -> set[str]:
    """Extract all imported names from a Python file."""
    if not filepath.exists():
        return set()
    tree = ast.parse(filepath.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    names.add(alias.name)
    return names


def _get_method_calls(filepath: Path) -> set[str]:
    """Extract all method-call attribute names from a Python file."""
    if not filepath.exists():
        return set()
    tree = ast.parse(filepath.read_text())
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


class TestInfraDoesNotImportLane2:
    """Infrastructure files must not import Lane 2 (Work) symbols."""

    @pytest.mark.parametrize("filename", INFRA_FILES)
    def test_no_lane2_imports(self, filename):
        filepath = SRC / filename
        if not filepath.exists():
            pytest.skip(f"{filename} does not exist yet")
        imports = _get_imports(filepath)
        violations = imports & LANE2_SYMBOLS
        assert not violations, (
            f"{filename} imports Lane 2 symbols: {violations}. "
            f"Infrastructure domain must only use models.core and models.health."
        )

    @pytest.mark.parametrize("filename", INFRA_FILES)
    def test_no_models_work_import(self, filename):
        """Check that infra files don't import from models.work directly."""
        filepath = SRC / filename
        if not filepath.exists():
            pytest.skip(f"{filename} does not exist yet")
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "models.work" not in node.module, (
                    f"{filename} imports from models.work. "
                    f"Infrastructure domain must only use models.core and models.health."
                )


class TestCodebaseDoesNotImportLane1:
    """Codebase files must not import Lane 1 (Health) symbols."""

    @pytest.mark.parametrize("filename", CODEBASE_FILES)
    def test_no_lane1_imports(self, filename):
        filepath = SRC / filename
        if not filepath.exists():
            pytest.skip(f"{filename} does not exist yet")
        imports = _get_imports(filepath)
        violations = imports & LANE1_SYMBOLS
        assert not violations, (
            f"{filename} imports Lane 1 symbols: {violations}. "
            f"Codebase domain must only use models.core and models.work."
        )

    @pytest.mark.parametrize("filename", CODEBASE_FILES)
    def test_no_save_evaluation_calls(self, filename):
        """Codebase files must never write to the evaluations table."""
        filepath = SRC / filename
        if not filepath.exists():
            pytest.skip(f"{filename} does not exist yet")
        calls = _get_method_calls(filepath)
        assert "save_evaluation" not in calls, (
            f"{filename} calls save_evaluation(). "
            f"Finding-level judgments must be stored as fields on WorkItem, "
            f"not as rows in the evaluations table."
        )

    @pytest.mark.parametrize("filename", CODEBASE_FILES)
    def test_no_models_health_import(self, filename):
        """Check that codebase files don't import from models.health directly."""
        filepath = SRC / filename
        if not filepath.exists():
            pytest.skip(f"{filename} does not exist yet")
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "models.health" not in node.module, (
                    f"{filename} imports from models.health. "
                    f"Codebase domain must only use models.core and models.work."
                )
