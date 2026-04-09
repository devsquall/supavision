"""Lane 2: Work models — per-issue lifecycle for codebase resources.

Codebase domain files (codebase_engine.py, scanner.py, agent_runner.py,
code_evaluator.py, prompt_builder.py, blocklist.py) should import from
here and from core.py ONLY.

These models must NEVER write to the evaluations table. Finding-level
judgments are stored as fields on the WorkItem itself (evaluation_verdict,
evaluation_reasoning, etc.), not as Evaluation records.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────────────


class FindingSeverity(enum.StrEnum):
    """Finding-level severity (distinct from resource-level Severity/health)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStage(enum.StrEnum):
    """Finding lifecycle — detection and assessment only."""

    CREATED = "created"
    SCANNED = "scanned"
    EVALUATED = "evaluated"
    DISMISSED = "dismissed"
    # Legacy stages — kept for backward-compatible deserialization only.
    # No UI paths lead here; no outbound transitions allowed.
    APPROVED = "approved"
    IMPLEMENTING = "implementing"
    COMPLETED = "completed"
    REJECTED = "rejected"


VALID_TRANSITIONS: dict[FindingStage, set[FindingStage]] = {
    FindingStage.CREATED: {FindingStage.EVALUATED, FindingStage.DISMISSED},
    FindingStage.SCANNED: {FindingStage.EVALUATED, FindingStage.DISMISSED},
    FindingStage.EVALUATED: {FindingStage.DISMISSED},
    FindingStage.DISMISSED: set(),
    # Legacy — terminal, no transitions out
    FindingStage.APPROVED: set(),
    FindingStage.IMPLEMENTING: set(),
    FindingStage.COMPLETED: set(),
    FindingStage.REJECTED: set(),
}


class FeedbackType(enum.StrEnum):
    FALSE_POSITIVE = "false_positive"
    NOT_WORTH_IT = "not_worth_it"
    NEEDS_CONTEXT = "needs_context"
    ALREADY_FIXED = "already_fixed"
    BY_DESIGN = "by_design"
    DUPLICATE = "duplicate"
    OTHER = "other"


class TaskSource(enum.StrEnum):
    SCANNER = "scanner"
    MANUAL = "manual"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCategory(enum.StrEnum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    FEATURE = "feature"
    BUG = "bug"
    IMPROVEMENT = "improvement"
    CUSTOM = "custom"


class Priority(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── Helpers ──────────────────────────────────────────────────────


def _short_id() -> str:
    return uuid4().hex[:12]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _do_transition(current: FindingStage, new_stage: FindingStage) -> FindingStage:
    """Validate and return new stage. Raises ValueError on invalid."""
    valid = VALID_TRANSITIONS.get(current, set())
    if new_stage not in valid:
        raise ValueError(
            f"Invalid transition: {current} -> {new_stage}. "
            f"Valid targets: {sorted(valid)}"
        )
    return new_stage


# ── Work Item Models ─────────────────────────────────────────────


class Finding(BaseModel):
    """Scanner-generated finding with code context."""

    id: str = Field(default_factory=_short_id)
    resource_id: str
    stage: FindingStage = FindingStage.SCANNED
    source: TaskSource = TaskSource.SCANNER
    category: str
    severity: FindingSeverity
    language: str
    file_path: str
    line_number: int
    snippet: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    pattern_name: str = ""
    run_id: str = ""
    # Evaluation fields (stored ON the WorkItem, not in the evaluations table)
    evaluation_verdict: str = ""
    evaluation_reasoning: str = ""
    evaluation_fix_approach: str = ""
    evaluation_effort: str = ""
    confidence: float = 0.0
    # Acknowledgement
    rejection_reason: str = ""
    blocklist_match: str = ""
    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def transition_to(self, new_stage: FindingStage) -> None:
        self.stage = _do_transition(self.stage, new_stage)
        self.updated_at = _utcnow()

    @property
    def display_title(self) -> str:
        return f"{self.category}: {self.file_path}:{self.line_number}"

    @property
    def dedup_signature(self) -> tuple[str, str]:
        """Deduplication key: (file_path, category)."""
        return (self.file_path, self.category)


class ManualTask(BaseModel):
    """Human-created task (feature request, bug, improvement)."""

    id: str = Field(default_factory=_short_id)
    resource_id: str
    stage: FindingStage = FindingStage.CREATED
    source: TaskSource = TaskSource.MANUAL
    title: str
    description: str = ""
    task_category: TaskCategory = TaskCategory.IMPROVEMENT
    priority: Priority = Priority.MEDIUM
    severity: FindingSeverity = FindingSeverity.MEDIUM
    file_path: str = ""
    line_number: int = 0
    # Evaluation fields (stored ON the WorkItem, not in the evaluations table)
    evaluation_verdict: str = ""
    evaluation_reasoning: str = ""
    evaluation_fix_approach: str = ""
    evaluation_effort: str = ""
    # Rejection
    rejection_reason: str = ""
    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def transition_to(self, new_stage: FindingStage) -> None:
        self.stage = _do_transition(self.stage, new_stage)
        self.updated_at = _utcnow()

    @property
    def display_title(self) -> str:
        return self.title

    @property
    def dedup_signature(self) -> tuple[str, str]:
        """Manual tasks are always unique."""
        return (self.id, self.id)


# Type alias for the discriminated union
WorkItem = Finding | ManualTask


# ── Supporting Models ────────────────────────────────────────────


class AgentJob(BaseModel):
    """Tracks an agent execution (evaluation, implementation, or scout)."""

    id: str = Field(default_factory=_short_id)
    work_item_id: str
    resource_id: str
    job_type: str
    status: JobStatus = JobStatus.PENDING
    pid: int = 0
    output: str = ""
    recording: str = ""  # JSON: list of [delay_ms, text] events for terminal replay
    result: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class Feedback(BaseModel):
    """User feedback on a work item rejection."""

    id: str = Field(default_factory=_short_id)
    work_item_id: str
    feedback_type: FeedbackType
    reason: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class Transition(BaseModel):
    """Audit trail entry for a work item stage change."""

    id: str = Field(default_factory=_short_id)
    work_item_id: str
    from_stage: str
    to_stage: str
    created_at: datetime = Field(default_factory=_utcnow)


class BlocklistEntry(BaseModel):
    """Learned false-positive pattern."""

    id: str = Field(default_factory=_short_id)
    pattern_signature: str
    category: str
    language: str
    description: str
    source_finding_id: str = ""
    match_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
