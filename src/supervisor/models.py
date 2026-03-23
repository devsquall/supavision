"""Supervisor — Core data models.

All modules reference types from this file. This is the foundation everything
else depends on.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────


class Severity(enum.StrEnum):
    """Report severity level assigned by the evaluator."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class RunType(enum.StrEnum):
    """Type of execution run."""

    DISCOVERY = "discovery"
    HEALTH_CHECK = "health_check"


class RunStatus(enum.StrEnum):
    """Lifecycle status of a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvalStrategy(enum.StrEnum):
    """How a report gets evaluated for severity."""

    LLM = "llm"  # Second Claude call evaluates the report
    KEYWORD = "keyword"  # Simple pattern matching on report text
    HYBRID = "hybrid"  # Keyword first, LLM for ambiguous cases


# ── Credential ───────────────────────────────────────────────────────


class Credential(BaseModel):
    """A named credential. Stores only the env var name — never the actual secret.

    At runtime, the engine reads os.environ[env_var] to get the value.
    This means credentials work with .env files, systemd env, or secret managers.
    """

    env_var: str = Field(description="Environment variable name holding the actual secret")
    description: str = ""


# ── Schedule ─────────────────────────────────────────────────────────


class Schedule(BaseModel):
    """Cron-like schedule for discovery or health check runs."""

    cron: str = Field(description="Cron expression, e.g. '0 */6 * * *' (every 6 hours)")
    enabled: bool = True


# ── Resource ─────────────────────────────────────────────────────────


class Resource(BaseModel):
    """A monitored resource. The fundamental unit of Supervisor.

    Resources form a parent-child tree. Children inherit credentials from parents
    (child overrides parent on name collision). The engine walks the parent chain
    at runtime to resolve the full credential set.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    resource_type: str = Field(
        description="Resource type identifier, e.g. 'aws_account', 'backup_policy', 'ec2_volumes'"
    )
    parent_id: str | None = Field(
        default=None,
        description="Parent resource ID. Children inherit parent credentials.",
    )
    credentials: dict[str, Credential] = Field(
        default_factory=dict,
        description="Named credentials. Key is the credential name (e.g. 'aws_access_key').",
    )
    config: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Arbitrary key-value config for template placeholders. "
            "E.g. {'region': 'us-east-1', 'account_id': '123456789012'}"
        ),
    )
    discovery_template: str = Field(
        default="",
        description="Template name or path for discovery. Defaults to '{resource_type}/discovery.md'",
    )
    health_check_template: str = Field(
        default="",
        description="Template name or path for health checks. Defaults to '{resource_type}/health_check.md'",
    )
    discovery_schedule: Schedule | None = None
    health_check_schedule: Schedule | None = None
    eval_strategy: EvalStrategy = EvalStrategy.LLM
    monitoring_requests: list[str] = Field(
        default_factory=list,
        description="Plain-language monitoring requests from team members",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def effective_discovery_template(self) -> str:
        return self.discovery_template or f"{self.resource_type}/discovery.md"

    @property
    def effective_health_check_template(self) -> str:
        return self.health_check_template or f"{self.resource_type}/health_check.md"


# ── System Context ───────────────────────────────────────────────────


class SystemContext(BaseModel):
    """Structured snapshot of what 'normal' looks like, produced by discovery.

    Versioned — each re-discovery creates a new version. The engine can diff
    version N vs N-1 for drift detection.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    resource_id: str
    content: str = Field(description="The full system context document (structured Markdown)")
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Checklist ────────────────────────────────────────────────────────


class ChecklistItem(BaseModel):
    """A single item to verify during health checks."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    description: str
    source: str = Field(
        default="discovery",
        description="Where this item came from: 'discovery' or 'team_request'",
    )
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Checklist(BaseModel):
    """Full checklist for a resource. Accumulates from discovery + team requests.

    Versioned — each re-discovery produces a new version with updated items.
    Team requests persist across versions.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    resource_id: str
    items: list[ChecklistItem] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Report ───────────────────────────────────────────────────────────


class Report(BaseModel):
    """Output of a discovery or health check run.

    Stores both the structured content (what the team reads) and the raw
    API response (for debugging).
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    resource_id: str
    run_type: RunType
    content: str = Field(description="The full report from Claude")
    raw_response: str = Field(default="", description="Raw API response for debugging")
    status: RunStatus = RunStatus.COMPLETED
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Evaluation ───────────────────────────────────────────────────────


class Evaluation(BaseModel):
    """Severity assessment of a report.

    Produced by the evaluator after each report. Determines whether the
    team should be alerted.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    report_id: str
    resource_id: str
    severity: Severity
    summary: str = Field(description="One-line explanation of the severity decision")
    should_alert: bool = False
    strategy_used: EvalStrategy = EvalStrategy.LLM
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Run ──────────────────────────────────────────────────────────────


class Run(BaseModel):
    """A single execution of discovery or health check.

    Tracks the full lifecycle: pending → running → completed/failed.
    Links to the report and evaluation produced by the run.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    resource_id: str
    run_type: RunType
    status: RunStatus = RunStatus.PENDING
    report_id: str | None = None
    evaluation_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
