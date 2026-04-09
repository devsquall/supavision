"""Supavision data models.

TWO-LANE ARCHITECTURE:
  Lane 1 (Health): Resource -> Run -> Report -> Evaluation
    Import from: models.core, models.health
    Files: engine.py, evaluator.py, executor.py, tools.py, discovery_diff.py

  Lane 2 (Work): Resource -> WorkItem (Finding | ManualTask) -> AgentJob
    Import from: models.core, models.work
    Files: codebase_engine.py, scanner.py, agent_runner.py, code_evaluator.py

See ARCHITECTURE.md for the full design rationale.

This __init__.py re-exports everything for convenience. However, domain-specific
code should import from the lane-specific submodule to make cross-lane imports
visible and enforceable by test_lane_boundary.py.
"""

# Core (shared by both lanes)
from .core import (
    Credential,
    EvalStrategy,
    Resource,
    Run,
    Session,
    User,
    RunStatus,
    RunType,
    Schedule,
)

# Lane 1: Health (resource-level monitoring)
from .health import (
    Checklist,
    ChecklistItem,
    Evaluation,
    Metric,
    Report,
    Severity,
    SystemContext,
)

# Lane 2: Work (per-issue lifecycle)
from .work import (
    VALID_TRANSITIONS,
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
    TaskSource,
    Transition,
    WorkItem,
)

__all__ = [
    # Core
    "Credential", "EvalStrategy", "Resource", "Run", "RunStatus", "RunType", "Schedule", "Session", "User",
    # Lane 1: Health
    "Checklist", "ChecklistItem", "Evaluation", "Metric", "Report", "Severity", "SystemContext",
    # Lane 2: Work
    "AgentJob", "BlocklistEntry", "Feedback", "FeedbackType", "Finding",
    "FindingSeverity", "FindingStage", "JobStatus", "ManualTask", "Priority",
    "TaskCategory", "TaskSource", "Transition", "VALID_TRANSITIONS", "WorkItem",
]
