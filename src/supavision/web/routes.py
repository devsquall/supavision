"""REST API routes for Supavision."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..models import (
    Credential,
    Incident,
    IncidentNote,
    IncidentState,
    Resource,
    RunType,
    Severity,
)
from ..models.health import IssueSeverity
from ..secrets_policy import (
    is_secret_key,
    validate_credentials_env_vars,
    validate_no_raw_secrets,
)
from .auth import get_auth_context, require_api_key, require_api_key_admin

logger = logging.getLogger(__name__)

_api_rate_limits: dict[str, list[float]] = defaultdict(list)


_API_RATE_LIMIT_PER_MINUTE = 60  # API consumers need higher throughput than dashboard


async def _api_rate_limit(request: Request):
    """FastAPI dependency: rate-limit mutating API requests per IP."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return  # Read-only requests are not rate-limited
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    _api_rate_limits[ip] = [t for t in _api_rate_limits[ip] if now - t < 60]
    if len(_api_rate_limits[ip]) >= _API_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _api_rate_limits[ip].append(now)


# Health endpoint — no auth required (for Docker healthcheck, load balancers, uptime monitors)
health_router = APIRouter(prefix="/api/v1")


@health_router.get("/health")
async def health():
    return {"status": "ok", "service": "supavision"}


@health_router.get("/search")
async def global_search(request: Request, q: str = ""):
    """Global search across resources. Accepts either session cookie or x-api-key.

    Lives under /api/v1/* which the dashboard session middleware skips for
    performance, so this handler validates both auth modes itself via
    ``get_auth_context``.
    """
    ctx = get_auth_context(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not q or len(q) < 2:
        return {"ok": True, "results": []}
    store = _get_store(request)
    results = []
    for r in store.list_resources():
        if q.lower() in r.name.lower() or q.lower() in r.resource_type.lower():
            results.append(
                {
                    "type": "resource",
                    "name": r.name,
                    "badge": r.resource_type,
                    "link": f"/resources/{r.id}",
                }
            )
    return {"ok": True, "results": results[:20]}


# All other API routes require API key auth
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key), Depends(_api_rate_limit)])


@router.get("/system/status")
async def system_status(request: Request):
    from .. import __version__
    from ..scheduler import get_scheduler_status

    return {
        "ok": True,
        "version": __version__,
        "scheduler": get_scheduler_status(),
    }


@router.get("/system/metrics")
async def system_metrics(request: Request):
    """Self-observability snapshot (P2 #16).

    Returns a small dict of operational metrics intended for ops dashboards
    and uptime monitors. Cheap reads only — no engine calls, no LLM round trips.
    """
    import os

    from .. import __version__
    from ..scheduler import get_scheduler_status

    store = _get_store(request)

    # Counts by run status (over the recent window)
    recent_runs = store.get_recent_runs_global(limit=200)
    counts: dict[str, int] = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    durations: list[float] = []
    failures_24h = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    for r in recent_runs:
        status = str(r.status)
        counts[status] = counts.get(status, 0) + 1
        if r.started_at and r.completed_at:
            durations.append((r.completed_at - r.started_at).total_seconds())
        if status == "failed" and r.completed_at and r.completed_at > cutoff:
            failures_24h += 1

    durations.sort()
    n = len(durations)

    def _pct(p: float) -> float | None:
        if not durations:
            return None
        idx = min(n - 1, int(p * n))
        return round(durations[idx], 2)

    db_size_bytes: int | None = None
    try:
        db_size_bytes = os.path.getsize(str(store.db_path))
    except OSError:
        pass

    # Notification delivery stats from the last 200 attempts
    notif_log = store.list_notifications(limit=200)
    notif_total = len(notif_log)
    notif_sent = sum(1 for n in notif_log if n.get("status") == "sent")
    notif_failed = notif_total - notif_sent

    return {
        "ok": True,
        "version": __version__,
        "scheduler": get_scheduler_status(),
        "runs": {
            "by_status": counts,
            "failures_24h": failures_24h,
            "duration_seconds": {
                "count": n,
                "p50": _pct(0.5),
                "p95": _pct(0.95),
            },
        },
        "notifications": {
            "total_recent": notif_total,
            "sent": notif_sent,
            "failed": notif_failed,
        },
        "db": {
            "size_bytes": db_size_bytes,
            "resources": len(store.list_resources()),
        },
    }


# ── Request/Response models ─────────────────────────────────────


class CreateResourceRequest(BaseModel):
    name: str = Field(..., max_length=200)
    resource_type: str
    parent_id: str = ""
    config: dict[str, str] = {}
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Credential references. Map of credential name (e.g. 'aws_secret_key') "
            "to the NAME of an environment variable holding the actual secret "
            "(e.g. 'AWS_SECRET_ACCESS_KEY'). Never pass the secret value itself."
        ),
    )

    @field_validator("config")
    @classmethod
    def validate_config(cls, v: dict) -> dict:
        if len(v) > 50:
            raise ValueError("config cannot have more than 50 entries")
        for k, val in v.items():
            if len(val) > 500:
                raise ValueError(f"config value for '{k}' must be 500 characters or fewer")
        return v


class UpdateResourceRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    config: dict | None = None
    credentials: dict[str, str] | None = Field(
        default=None,
        description=("Credential references to merge in. Each value must be an env var name."),
    )
    parent_id: str | None = None


def _reject_raw_secrets_or_400(config: dict | None, credentials: dict[str, str] | None) -> None:
    """Raise 400 with a structured error if the request carries raw secrets.

    Splits the two failure modes (raw secret in config / non-env-var-shaped
    credential reference) into distinct error codes so API clients can act on
    them programmatically.
    """
    raw_offenders = validate_no_raw_secrets(config)
    if raw_offenders:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "raw_secrets_in_config",
                "fields": raw_offenders,
                "hint": (
                    "Pass these in the top-level `credentials` object as env-var names, not as raw values in `config`."
                ),
            },
        )
    bad_env = validate_credentials_env_vars(credentials)
    if bad_env:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_env_var_name_in_credentials",
                "fields": bad_env,
                "hint": (
                    "Credential references must be env var names (matching ^[A-Z_][A-Z0-9_]*$), not raw secret values."
                ),
            },
        )


class TriggerRunRequest(BaseModel):
    """Request body for POST /runs (Workstream E3)."""

    resource_id: str
    run_type: str = "health_check"  # "discovery" or "health_check"


class ResourceSummary(BaseModel):
    id: str
    name: str
    resource_type: str
    created_at: str | None = None
    latest_severity: str | None = None
    latest_run_status: str | None = None


# ── Helper ──────────────────────────────────────────────────────


def _get_store(request: Request):
    return request.app.state.store


def _get_engine(request: Request):
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Infrastructure monitoring unavailable. Install Claude CLI to enable.",
        )
    return engine


# ── Resources ───────────────────────────────────────────────────


@router.get("/resources")
async def list_resources(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    type: str = "",
):
    store = _get_store(request)
    # Clamp limit to [1, 100] per plan risk #5
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    resources, total = store.list_resources_paginated(
        limit=limit,
        offset=offset,
        resource_type=type or None,
    )
    latest_runs = store.get_latest_runs_batch()
    latest_evals = store.get_latest_evaluations_batch()

    result = []
    for r in resources:
        latest = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        ev = latest_evals.get(r.id)
        result.append(
            {
                "id": r.id,
                "name": r.name,
                "resource_type": r.resource_type,
                "parent_id": r.parent_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "latest_severity": str(ev.severity) if ev else None,
                "latest_run_status": str(latest.status) if latest else None,
            }
        )

    from starlette.responses import JSONResponse

    return JSONResponse(
        content={"ok": True, "resources": result, "total": total},
        headers={"X-Total-Count": str(total)},
    )


@router.post("/resources")
async def create_resource(body: CreateResourceRequest, request: Request, _admin=Depends(require_api_key_admin)):
    _reject_raw_secrets_or_400(body.config, body.credentials)
    store = _get_store(request)
    resource = Resource(
        name=body.name,
        resource_type=body.resource_type,
        parent_id=body.parent_id or "",
        config=body.config,
        credentials={
            name: Credential(env_var=env_var.strip())
            for name, env_var in (body.credentials or {}).items()
            if env_var and env_var.strip()
        },
    )
    store.save_resource(resource)
    return {"ok": True, "resource_id": resource.id, "name": resource.name}


@router.get("/resources/{resource_id}")
async def get_resource(resource_id: str, request: Request):
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    context = store.get_latest_context(resource_id)
    checklist = store.get_latest_checklist(resource_id)
    recent_runs = store.get_runs(resource_id, limit=5)

    # Filter secret-shaped fields and the internal _last_alert_key marker
    # from the response. Legacy rows may still hold raw secrets in config
    # (we don't migrate on load); the API must never serve them back.
    resource_data = resource.model_dump(mode="json")
    resource_data["config"] = {
        k: v for k, v in resource_data.get("config", {}).items() if not is_secret_key(k) and k != "_last_alert_key"
    }

    return {
        "ok": True,
        "resource": resource_data,
        "context": context.model_dump(mode="json") if context else None,
        "checklist": checklist.model_dump(mode="json") if checklist else None,
        "recent_runs": [r.model_dump(mode="json") for r in recent_runs],
    }


@router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    store.delete_resource(resource_id)
    return {"ok": True, "deleted": resource_id}


@router.put("/resources/{resource_id}")
async def update_resource(
    resource_id: str, body: UpdateResourceRequest, request: Request, _admin=Depends(require_api_key_admin)
):
    _reject_raw_secrets_or_400(body.config, body.credentials)
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if body.name is not None:
        resource.name = body.name
    if body.config is not None:
        resource.config.update(body.config)
    if body.credentials is not None:
        for cred_name, env_var in body.credentials.items():
            ev = (env_var or "").strip()
            if ev:
                resource.credentials[cred_name] = Credential(env_var=ev)
            else:
                resource.credentials.pop(cred_name, None)
    if body.parent_id is not None:
        resource.parent_id = body.parent_id
    resource.updated_at = datetime.now(timezone.utc)
    store.save_resource(resource)
    return {"ok": True, "resource_id": resource.id, "name": resource.name}


# ── Trigger Runs ────────────────────────────────────────────────


# Trigger logic lives in web/run_triggers.py — single source of truth shared
# by the API and the dashboard. Re-export here for backwards-compat imports.
from .run_triggers import trigger_run_or_409 as _trigger_run_or_409  # noqa: E402


@router.post("/resources/{resource_id}/discover")
async def trigger_discovery(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    engine = _get_engine(request)
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return _trigger_run_or_409(store, engine, resource_id, RunType.DISCOVERY)


@router.post("/resources/{resource_id}/health-check")
async def trigger_health_check(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    engine = _get_engine(request)
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return _trigger_run_or_409(store, engine, resource_id, RunType.HEALTH_CHECK)


# Workstream E3: unified run trigger
@router.post("/runs")
async def trigger_run(body: TriggerRunRequest, request: Request, _admin=Depends(require_api_key_admin)):
    """Trigger a discovery or health-check run via API.

    Returns immediately with the run_id of the executing run. The run executes
    in the background. Returns 409 Conflict (with active_run_id) if a run is
    already in progress for the resource.
    """
    store = _get_store(request)
    engine = _get_engine(request)

    if not store.get_resource(body.resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    if body.run_type not in ("discovery", "health_check"):
        raise HTTPException(status_code=400, detail="run_type must be 'discovery' or 'health_check'")

    rt = RunType.DISCOVERY if body.run_type == "discovery" else RunType.HEALTH_CHECK
    return _trigger_run_or_409(store, engine, body.resource_id, rt)


# ── Runs & Reports ──────────────────────────────────────────────


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    store = _get_store(request)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    result = run.model_dump(mode="json")

    # Attach report and evaluation if completed
    if run.report_id:
        report = store.get_report(run.report_id)
        if report:
            result["report"] = report.model_dump(mode="json")

    if run.evaluation_id:
        evaluation = store.get_evaluation(run.evaluation_id)
        if evaluation:
            result["evaluation"] = evaluation.model_dump(mode="json")

    return {"ok": True, "run": result}


@router.get("/reports")
async def list_reports(
    request: Request,
    resource_id: str = "",
    run_type: str = "health_check",
    limit: int = 20,
    offset: int = 0,
):
    store = _get_store(request)
    if not resource_id:
        raise HTTPException(status_code=400, detail="resource_id query parameter required")

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    # Fetch limit+offset+1 to approximate total without a separate count query
    reports = store.get_recent_reports(resource_id, RunType(run_type), limit=limit + offset + 1)
    total = len(reports)
    page = reports[offset : offset + limit]
    return {
        "ok": True,
        "reports": [r.model_dump(mode="json") for r in page],
        "total": total,
    }


# ── Notifications ───────────────────────────────────────────────


@router.post("/resources/{resource_id}/notify-test")
async def notify_test(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    from ..models import Evaluation, Report
    from ..notifications import send_alert

    test_report = Report(
        resource_id=resource.id,
        run_type=RunType.HEALTH_CHECK,
        content="Test notification from Supavision API.",
    )
    test_eval = Evaluation(
        report_id=test_report.id,
        resource_id=resource.id,
        severity=Severity.WARNING,
        summary="Test notification — verifying webhook configuration",
        should_alert=True,
    )

    channels, _ = await send_alert(resource, test_report, test_eval, skip_dedup=True)
    return {"ok": bool(channels), "channels": channels}


@router.get("/resources/{resource_id}/metrics")
async def get_resource_metrics(resource_id: str, request: Request):
    """Get latest structured metrics for a resource."""
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    metrics = store.get_latest_metrics(resource_id)
    return {"ok": True, "resource_id": resource_id, "metrics": metrics}


@router.get("/resources/{resource_id}/metrics/{metric_name}")
async def get_metric_trend(resource_id: str, metric_name: str, request: Request, days: int = 30):
    """Get time-series history for a specific metric."""
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    history = store.get_metrics_history(resource_id, metric_name, days=min(days, 90))
    return {"ok": True, "resource_id": resource_id, "metric": metric_name, "days": days, "data": history}


@router.get("/resources/{resource_id}/incidents")
async def get_incidents(resource_id: str, request: Request, limit: int = 10):
    """Get severity change timeline for a resource (incident history)."""
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    evaluations = store.get_recent_evaluations(resource_id, limit=limit * 2)
    if not evaluations:
        return {"ok": True, "resource_id": resource_id, "incidents": []}

    # Build timeline of severity transitions
    incidents = []
    prev_severity = None
    for ev in reversed(evaluations):  # oldest first
        if prev_severity and str(ev.severity) != prev_severity:
            incidents.append(
                {
                    "timestamp": str(ev.created_at),
                    "from_severity": prev_severity,
                    "to_severity": str(ev.severity),
                    "summary": ev.summary,
                    "correlation": ev.correlation,
                }
            )
        prev_severity = str(ev.severity)

    # Most recent first
    incidents.reverse()
    return {"ok": True, "resource_id": resource_id, "incidents": incidents[:limit]}


# ── Incidents (P2 #12) ────────────────────────────────────────────


class CreateIncidentRequest(BaseModel):
    resource_id: str
    title: str = Field(..., min_length=1, max_length=200)
    # Enum: pydantic validates "warning"/"critical"/"info" → returns IssueSeverity;
    # anything else → 422 ValidationError (FastAPI returns 422 by default).
    severity: IssueSeverity = IssueSeverity.WARNING
    evaluation_id: str | None = None


class IncidentTransitionRequest(BaseModel):
    """Body for ack/snooze/resolve/assign endpoints (only the relevant fields are read)."""

    owner_user_id: str | None = None
    snoozed_until: datetime | None = None
    note: str | None = None

    @field_validator("snoozed_until")
    @classmethod
    def _future_only(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        # Compare in UTC so naive timestamps are treated as UTC for the check.
        comparable = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if comparable <= datetime.now(timezone.utc):
            raise ValueError("snoozed_until must be in the future")
        return v


def _serialize_incident(inc: Incident) -> dict:
    return {
        "id": inc.id,
        "resource_id": inc.resource_id,
        "title": inc.title,
        "state": str(inc.state),
        "severity": inc.severity,
        "owner_user_id": inc.owner_user_id,
        "snoozed_until": inc.snoozed_until.isoformat() if inc.snoozed_until else None,
        "evaluation_id": inc.evaluation_id,
        "notes": [{"author": n.author, "text": n.text, "created_at": n.created_at.isoformat()} for n in inc.notes],
        "created_at": inc.created_at.isoformat(),
        "updated_at": inc.updated_at.isoformat(),
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


@router.get("/incidents")
async def list_incidents(request: Request, resource_id: str = "", state: str = "", limit: int = 50):
    """List incidents, optionally filtered by resource_id and/or state."""
    store = _get_store(request)
    limit = max(1, min(limit, 200))
    incidents = store.list_incidents(
        resource_id=resource_id or None,
        state=state or None,
        limit=limit,
    )
    return {"ok": True, "incidents": [_serialize_incident(i) for i in incidents]}


@router.post("/incidents")
async def create_incident(body: CreateIncidentRequest, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    if not store.get_resource(body.resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    # If an evaluation is linked, verify it exists AND belongs to this resource
    # (otherwise the incident would reference a stranger eval). Cheap single lookup.
    if body.evaluation_id:
        ev = store.get_evaluation(body.evaluation_id)
        if ev is None:
            raise HTTPException(
                status_code=400,
                detail={"error": "unknown_evaluation_id", "evaluation_id": body.evaluation_id},
            )
        if ev.resource_id != body.resource_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "evaluation_resource_mismatch",
                    "evaluation_id": body.evaluation_id,
                    "evaluation_resource_id": ev.resource_id,
                    "incident_resource_id": body.resource_id,
                },
            )

    inc = Incident(
        resource_id=body.resource_id,
        title=body.title,
        severity=str(body.severity),
        evaluation_id=body.evaluation_id,
    )
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}


def _verify_user_or_400(store, user_id: str) -> None:
    """Ensure `user_id` exists in the users table; raise 400 if not."""
    if not user_id:
        return
    if store.get_user(user_id) is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown_owner_user_id", "owner_user_id": user_id},
        )


def _get_incident_or_404(store, incident_id: str) -> Incident:
    inc = store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


def _touch(inc: Incident, *, note: str | None = None, author: str = "api") -> None:
    inc.updated_at = datetime.now(timezone.utc)
    if note:
        inc.notes.append(IncidentNote(author=author, text=note))


@router.post("/incidents/{incident_id}/acknowledge")
async def incident_acknowledge(
    incident_id: str,
    body: IncidentTransitionRequest,
    request: Request,
    _admin=Depends(require_api_key_admin),
):
    store = _get_store(request)
    if body.owner_user_id:
        _verify_user_or_400(store, body.owner_user_id)
    inc = _get_incident_or_404(store, incident_id)
    inc.state = IncidentState.ACKNOWLEDGED
    if body.owner_user_id:
        inc.owner_user_id = body.owner_user_id
    _touch(inc, note=body.note)
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}


@router.post("/incidents/{incident_id}/assign")
async def incident_assign(
    incident_id: str,
    body: IncidentTransitionRequest,
    request: Request,
    _admin=Depends(require_api_key_admin),
):
    if not body.owner_user_id:
        raise HTTPException(status_code=400, detail="owner_user_id is required")
    store = _get_store(request)
    _verify_user_or_400(store, body.owner_user_id)
    inc = _get_incident_or_404(store, incident_id)
    inc.owner_user_id = body.owner_user_id
    _touch(inc, note=body.note)
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}


@router.post("/incidents/{incident_id}/snooze")
async def incident_snooze(
    incident_id: str,
    body: IncidentTransitionRequest,
    request: Request,
    _admin=Depends(require_api_key_admin),
):
    if not body.snoozed_until:
        raise HTTPException(status_code=400, detail="snoozed_until is required")
    store = _get_store(request)
    inc = _get_incident_or_404(store, incident_id)
    inc.state = IncidentState.SNOOZED
    inc.snoozed_until = body.snoozed_until
    _touch(inc, note=body.note)
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}


@router.post("/incidents/{incident_id}/note")
async def incident_add_note(
    incident_id: str,
    body: IncidentTransitionRequest,
    request: Request,
    _admin=Depends(require_api_key_admin),
):
    if not body.note:
        raise HTTPException(status_code=400, detail="note is required")
    store = _get_store(request)
    inc = _get_incident_or_404(store, incident_id)
    _touch(inc, note=body.note)
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}


@router.post("/incidents/{incident_id}/resolve")
async def incident_resolve(
    incident_id: str,
    body: IncidentTransitionRequest,
    request: Request,
    _admin=Depends(require_api_key_admin),
):
    store = _get_store(request)
    inc = _get_incident_or_404(store, incident_id)
    inc.state = IncidentState.RESOLVED
    inc.resolved_at = datetime.now(timezone.utc)
    _touch(inc, note=body.note)
    store.save_incident(inc)
    return {"ok": True, "incident": _serialize_incident(inc)}
