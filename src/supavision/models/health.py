"""Lane 1: Health models — resource-level monitoring.

Infrastructure domain files (engine.py, evaluator.py, executor.py, tools.py,
discovery_diff.py) should import from here and from core.py ONLY.

These models must NEVER be used for per-issue lifecycle tracking.
For that, see work.py (Lane 2).
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

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

    Workstream A3: `payload` holds the structured fields Claude submitted via
    `submit_report` (Lane 1 health checks that opt in via `report_vocab`).
    When `payload is None`, the report is in legacy prose-only mode and the
    dashboard / evaluator / alerts fall back to the pre-A behavior.
    `run_metadata` is engine-stamped (template version, runtime, tool-call count)
    and is independent of whether Claude submitted a payload.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    run_type: RunType
    content: str = Field(description="The full report from Claude")
    status: str = "completed"
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Workstream A3 — both nullable for backwards compatibility with pre-A reports.
    payload: "ReportPayload | None" = Field(
        default=None,
        description="Structured payload from submit_report (None for legacy/incomplete runs)",
    )
    run_metadata: "RunMetadata | None" = Field(
        default=None,
        description="Engine-stamped run metadata (template_version, runtime, tool count)",
    )
    # Workstream A6 — engine-computed set-diff vs the previous run's payload.
    payload_diff: "IssueDiff | None" = Field(
        default=None,
        description="Issue diff vs most recent prior payload (None for first structured run)",
    )


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


# ── Structured Report Payload (Workstream A) ─────────────────────────
#
# Claude submits structured report output via the `submit_report` tool at the
# end of a Lane 1 health check. The tool's argument schema is `ReportPayload`;
# Pydantic validates at the tool boundary, so there is no prose parsing.
# Engine-stamped fields (template version, runtime) live on `RunMetadata`
# and are NOT part of what Claude provides.


class PayloadStatus(enum.StrEnum):
    """Status reported by Claude in the submit_report payload.

    Distinct from `Severity` because this enum includes UNKNOWN — used when
    Claude never called submit_report (ran out of turns, errored, etc.).
    The evaluator resolves UNKNOWN back to a concrete Severity via the
    regex fallback path.
    """

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class IssueSeverity(enum.StrEnum):
    """Per-issue severity inside a report payload."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


def _slugify(text: str) -> str:
    """Canonical slug: lowercase, non-alphanumeric → '-', trimmed, max 80 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "unknown"


def compute_issue_id(tags: list[str], scope: str | None, title: str) -> str:
    """Derive a stable issue id from (primary_tag, scope), with a title fallback.

    The diff mechanism in Workstream A depends on this id being stable run-over-run
    so set-diff can compute new/resolved/persisted issues. Tags are the canonical
    category vocabulary per resource type (declared in the prompt preamble). Scope
    narrows the tag to a specific resource-local entity (e.g., a filesystem path,
    service name, or hostname). Title is used only as a last resort and will
    produce cosmetic churn if Claude rewords the same issue.
    """
    if tags:
        primary = tags[0].strip().lower()
        base = f"{primary}:{scope.strip().lower()}" if scope else primary
    else:
        base = title
    return _slugify(base)


class ReportIssue(BaseModel):
    """A single issue identified in a Lane 1 health report.

    Claude provides title/severity/evidence/recommendation/tags/scope; the
    engine derives `id` from (tags, scope) so diffs stay stable even when
    Claude rephrases the title.
    """

    id: str = Field(default="", description="Stable slug; derived if blank")
    title: str = Field(min_length=1, max_length=200)
    severity: IssueSeverity
    evidence: str = Field(default="", max_length=2000)
    recommendation: str = Field(default="", max_length=1000)
    tags: list[str] = Field(
        default_factory=list,
        description="Canonical category tags (e.g., 'disk', 'cert-expiry', 'brute-force')",
    )
    scope: str | None = Field(
        default=None,
        max_length=200,
        description="Resource-local scope: filesystem path, service name, hostname, etc.",
    )

    @model_validator(mode="after")
    def _ensure_stable_id(self) -> ReportIssue:
        if not self.id:
            self.id = compute_issue_id(self.tags, self.scope, self.title)
        return self


class ReportPayload(BaseModel):
    """Structured output Claude submits via the `submit_report` tool.

    This is the contract that turns Lane 1 reports from prose dumps into
    structured data. See plan: purrfect-inventing-nebula.md, Workstream A.

    Engine-stamped metadata (template version, runtime, tool-call count)
    lives on `RunMetadata` and is *not* part of this model — Claude does
    not fill it in.
    """

    status: PayloadStatus
    summary: str = Field(min_length=1, max_length=500, description="1–3 sentence TL;DR")
    metrics: dict[str, float | int | str] = Field(
        default_factory=dict,
        description=(
            "Typed gauges keyed by canonical name. Names/units are validated "
            "against metric_schemas.py during engine persistence (Workstream A3)."
        ),
    )
    issues: list[ReportIssue] = Field(default_factory=list)


class RunMetadata(BaseModel):
    """Engine-stamped metadata about a report run. NOT submitted by Claude.

    Populated by the engine after the subprocess completes and before the
    Report is persisted.
    """

    template_version: str = ""
    tool_calls_made: int = 0
    runtime_seconds: float = 0.0


# ── Workstream A6: run-vs-previous issue diff ────────────────────────
#
# Engine-computed set-diff over stable issue ids between the current run's
# payload and the most recent previous run's payload for the same resource.
# Populated by `compute_issue_diff`; None when there is no prior payload to
# compare against (first structured run after opt-in).


class IssueDiffEntry(BaseModel):
    """A single issue reference in a diff, with enough data to render."""

    id: str
    title: str
    severity: IssueSeverity


class IssueDiff(BaseModel):
    """Set-diff between current and previous run payloads.

    `new`: issues present in current that were absent in previous.
    `resolved`: issues present in previous that are absent in current.
    `persisted`: issues present in both (referenced by stable id).

    `compared_against_report_id` records which previous report this diff was
    computed against — useful for debugging "why did diff regress" cases.
    """

    new: list[IssueDiffEntry] = Field(default_factory=list)
    resolved: list[IssueDiffEntry] = Field(default_factory=list)
    persisted: list[IssueDiffEntry] = Field(default_factory=list)
    compared_against_report_id: str | None = None

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.resolved)

    @property
    def total_current(self) -> int:
        return len(self.new) + len(self.persisted)


def compute_issue_diff(
    current: ReportPayload,
    previous: ReportPayload | None,
    compared_against_report_id: str | None = None,
) -> IssueDiff:
    """Set-diff over stable issue ids.

    Called by the engine at save time; pure and deterministic. `previous=None`
    yields a diff where everything in `current` is "new" (first run scenario).
    """
    if previous is None:
        return IssueDiff(
            new=[
                IssueDiffEntry(id=i.id, title=i.title, severity=i.severity)
                for i in current.issues
            ],
            resolved=[],
            persisted=[],
            compared_against_report_id=None,
        )

    curr_by_id: dict[str, ReportIssue] = {i.id: i for i in current.issues}
    prev_by_id: dict[str, ReportIssue] = {i.id: i for i in previous.issues}
    curr_ids = set(curr_by_id)
    prev_ids = set(prev_by_id)

    new_ids = curr_ids - prev_ids
    resolved_ids = prev_ids - curr_ids
    persisted_ids = curr_ids & prev_ids

    def _entry(source: dict[str, ReportIssue], issue_id: str) -> IssueDiffEntry:
        issue = source[issue_id]
        return IssueDiffEntry(id=issue_id, title=issue.title, severity=issue.severity)

    return IssueDiff(
        new=[_entry(curr_by_id, i) for i in sorted(new_ids)],
        resolved=[_entry(prev_by_id, i) for i in sorted(resolved_ids)],
        # For persisted, take the *current* view (titles may have evolved
        # but the id is stable — see R6).
        persisted=[_entry(curr_by_id, i) for i in sorted(persisted_ids)],
        compared_against_report_id=compared_against_report_id,
    )


# Resolve forward references on `Report` now that `ReportPayload`,
# `RunMetadata`, and `IssueDiff` are defined (A3, A6).
Report.model_rebuild()
