"""Core models shared across all modules."""

from __future__ import annotations

import enum
import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# ── Enums ────────────────────────────────────────────────────────────


class RunType(enum.StrEnum):
    """Type of execution run."""

    DISCOVERY = "discovery"
    HEALTH_CHECK = "health_check"
    SCAN = "scan"  # Legacy — no longer created, kept for DB backward compat


class RunStatus(enum.StrEnum):
    """Lifecycle status of a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvalStrategy(enum.StrEnum):
    """How a report gets evaluated for severity."""

    LLM = "llm"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


# ── Credential ───────────────────────────────────────────────────────


class Credential(BaseModel):
    """A named credential. Stores only the env var name — never the actual secret."""

    env_var: str = Field(description="Environment variable name holding the actual secret")
    description: str = ""


# ── Schedule ─────────────────────────────────────────────────────────


class Schedule(BaseModel):
    """Cron-like schedule for discovery or health check runs."""

    cron: str = Field(description="Cron expression, e.g. '0 */6 * * *' (every 6 hours)")
    enabled: bool = True


# ── Resource ─────────────────────────────────────────────────────────


class Resource(BaseModel):
    """A monitored resource. The fundamental unit of Supavision.

    Resources form a parent-child tree. Children inherit credentials from parents
    (child overrides parent on name collision).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    resource_type: str = Field(
        description="Resource type identifier, e.g. 'aws_account', 'server', 'database'"
    )

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str) -> str:
        """Prevent path traversal — resource_type is used in template file paths."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                f"resource_type must contain only alphanumeric characters, underscores, "
                f"and hyphens (got: {v!r})"
            )
        return v
    parent_id: str | None = Field(
        default=None,
        description="Parent resource ID. Children inherit parent credentials.",
    )
    credentials: dict[str, Credential] = Field(
        default_factory=dict,
        description="Named credentials. Key is the credential name.",
    )
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key-value config for template placeholders.",
    )
    discovery_template: str = Field(
        default="",
        description="Template name or path for discovery.",
    )
    health_check_template: str = Field(
        default="",
        description="Template name or path for health checks.",
    )
    discovery_schedule: Schedule | None = None
    health_check_schedule: Schedule | None = None
    eval_strategy: EvalStrategy = EvalStrategy.LLM
    monitoring_requests: list[str] = Field(
        default_factory=list,
        description="Plain-language monitoring requests from team members",
    )
    enabled: bool = Field(default=True, description="Whether scheduled monitoring is active")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def effective_discovery_template(self) -> str:
        return self.discovery_template or f"{self.resource_type}/discovery.md"

    @property
    def effective_health_check_template(self) -> str:
        return self.health_check_template or f"{self.resource_type}/health_check.md"


# ── Run ──────────────────────────────────────────────────────────────


class Run(BaseModel):
    """A single execution of discovery, health check, or scan.

    Tracks the full lifecycle: pending → running → completed/failed.
    Links to the report and evaluation produced by the run.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    run_type: RunType
    status: RunStatus = RunStatus.PENDING
    report_id: str | None = None
    evaluation_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    output: str = ""       # Plain text output for display/search (capped 100KB)
    recording: str = ""    # JSON: list of [delay_ms, text] events for terminal replay
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Auth models ─────────────────────────────────────────────────────


class User(BaseModel):
    """A dashboard user with role-based access."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    password_hash: str
    name: str = ""
    role: str = "viewer"  # "admin" or "viewer"
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = None


class Session(BaseModel):
    """An authenticated dashboard session with CSRF token and idle tracking."""

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    user_id: str
    csrf_token: str = Field(default_factory=lambda: secrets.token_hex(16))
    ip_address: str = ""
    user_agent: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + __import__("datetime").timedelta(hours=8)
    )
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None
