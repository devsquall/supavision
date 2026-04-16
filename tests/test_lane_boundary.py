"""Lane boundary enforcement tests.

Verifies that infrastructure domain files (Lane 1) do not import Lane 2 models.
Lane 2 (codebase / findings) was removed in v0.4.0 — the reverse-direction
tests are also gone. These tests still guard against Lane 2 being accidentally
re-introduced if those modules ever come back.

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

# Lane 2 codebase domain was removed in v0.4.0. CODEBASE_FILES kept empty
# intentionally — if Lane 2 returns, re-add file names here.
_REMOVED_LANE2_FILES = [
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


# (TestCodebaseDoesNotImportLane1 removed in v0.4.0 — Lane 2 files no longer exist.)
