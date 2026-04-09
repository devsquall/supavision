"""Lane 1: Health models — resource-level monitoring.

Infrastructure domain files (engine.py, evaluator.py, executor.py, tools.py,
discovery_diff.py) should import from here and from core.py ONLY.

These models must NEVER be used for per-issue lifecycle tracking.
For that, see work.py (Lane 2).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from .core import RunType


class Severity(enum.StrEnum):
    """Report severity level assigned by the evaluator.

    This is resource-level health: "Is this resource healthy?"
    NOT finding-level severity. For that, use FindingSeverity in work.py.
    """

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


# ── System Context ───────────────────────────────────────────────────


class SystemContext(BaseModel):
    """Structured snapshot of what 'normal' looks like, produced by discovery."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    content: str = Field(description="The full system context document (structured Markdown)")
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Checklist ────────────────────────────────────────────────────────


class ChecklistItem(BaseModel):
    """A single item to verify during health checks."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    source: str = Field(
        default="discovery",
        description="Where this item came from: 'discovery' or 'team_request'",
    )
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Checklist(BaseModel):
    """Full checklist for a resource. Accumulates from discovery + team requests."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    items: list[ChecklistItem] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Report ───────────────────────────────────────────────────────────


class Report(BaseModel):
    """Output of a discovery or health check run.

    One Report per Run. This is an aggregate narrative about resource state,
    NOT a per-issue tracker. Reports do NOT have lifecycle stages.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    run_type: RunType
    content: str = Field(description="The full report from Claude")
    status: str = "completed"
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Evaluation ───────────────────────────────────────────────────────


class Evaluation(BaseModel):
    """Severity assessment of a Report.

    Answers: "How healthy is this resource?"
    Uses Severity (healthy/warning/critical).

    This is ONLY for resource-level Report evaluations (Lane 1).
    Finding-level judgments are stored as fields on WorkItem itself (Lane 2).
    The evaluations table must NEVER contain finding-level data.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    report_id: str
    resource_id: str
    severity: Severity
    summary: str = Field(description="One-line explanation of the severity decision")
    should_alert: bool = False
    strategy_used: str = "llm"
    correlation: str | None = Field(default=None, description="Cross-resource correlation context")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Metric(BaseModel):
    """A single structured measurement from a health check.

    Validated against per-resource-type schemas in metric_schemas.py
    before saving. Unknown names rejected, ranges enforced.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    report_id: str
    name: str = Field(description="Schema-validated metric name (e.g., cpu_percent)")
    value: float
    unit: str = Field(default="", description="Unit from schema (%, GB, USD, count, seconds)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
