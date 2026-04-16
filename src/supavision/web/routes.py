"""REST API routes for Supavision."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..models import (
    Resource,
    RunType,
    Severity,
)
from .auth import require_api_key, require_api_key_admin

logger = logging.getLogger(__name__)

# Health endpoint — no auth required (for Docker healthcheck, load balancers, uptime monitors)
health_router = APIRouter(prefix="/api/v1")


@health_router.get("/health")
async def health():
    return {"status": "ok", "service": "supavision"}


@health_router.get("/search")
async def global_search(request: Request, q: str = ""):
    """Global search across resources."""
    if not q or len(q) < 2:
        return {"ok": True, "results": []}
    store = _get_store(request)
    results = []
    for r in store.list_resources():
        if q.lower() in r.name.lower() or q.lower() in r.resource_type.lower():
            results.append({
                "type": "resource",
                "name": r.name,
                "badge": r.resource_type,
                "link": f"/resources/{r.id}",
            })
    return {"ok": True, "results": results[:20]}


# All other API routes require API key auth
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/system/status")
async def system_status(request: Request):
    from .. import __version__
    from ..scheduler import get_scheduler_status

    return {
        "ok": True,
        "version": __version__,
        "scheduler": get_scheduler_status(),
    }


# ── Request/Response models ─────────────────────────────────────


class CreateResourceRequest(BaseModel):
    name: str
    resource_type: str
    parent_id: str = ""
    config: dict[str, str] = {}


class UpdateResourceRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    parent_id: str | None = None


class TriggerResponse(BaseModel):
    ok: bool = True
    run_id: str


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
        result.append({
            "id": r.id,
            "name": r.name,
            "resource_type": r.resource_type,
            "parent_id": r.parent_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "latest_severity": str(ev.severity) if ev else None,
            "latest_run_status": str(latest.status) if latest else None,
        })

    from starlette.responses import JSONResponse

    return JSONResponse(
        content={"ok": True, "resources": result, "total": total},
        headers={"X-Total-Count": str(total)},
    )


@router.post("/resources")
async def create_resource(body: CreateResourceRequest, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    resource = Resource(
        name=body.name,
        resource_type=body.resource_type,
        parent_id=body.parent_id or "",
        config=body.config,
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

    # Filter sensitive fields from config before returning
    resource_data = resource.model_dump(mode="json")
    sensitive_keys = {"ssh_key_path", "slack_webhook", "_last_alert_key"}
    resource_data["config"] = {
        k: v for k, v in resource_data.get("config", {}).items()
        if k not in sensitive_keys
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
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if body.name is not None:
        resource.name = body.name
    if body.config is not None:
        resource.config.update(body.config)
    if body.parent_id is not None:
        resource.parent_id = body.parent_id
    resource.updated_at = datetime.now(timezone.utc)
    store.save_resource(resource)
    return {"ok": True, "resource_id": resource.id, "name": resource.name}


# ── Trigger Runs ────────────────────────────────────────────────


@router.post("/resources/{resource_id}/discover")
async def trigger_discovery(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    engine = _get_engine(request)

    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # Create a pending run and return immediately
    from ..models import Run, RunStatus

    run = Run(resource_id=resource_id, run_type=RunType.DISCOVERY, status=RunStatus.PENDING)
    store.save_run(run)

    # Execute in background
    async def _run():
        try:
            await engine.run_discovery_async(resource_id)
        except Exception as e:
            logger.error("Background discovery failed: %s", e)

    asyncio.create_task(_run())
    return TriggerResponse(run_id=run.id)


@router.post("/resources/{resource_id}/health-check")
async def trigger_health_check(resource_id: str, request: Request, _admin=Depends(require_api_key_admin)):
    store = _get_store(request)
    engine = _get_engine(request)

    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    from ..models import Run, RunStatus

    run = Run(resource_id=resource_id, run_type=RunType.HEALTH_CHECK, status=RunStatus.PENDING)
    store.save_run(run)

    async def _run():
        try:
            await engine.run_health_check_async(resource_id)
        except Exception as e:
            logger.error("Background health check failed: %s", e)

    asyncio.create_task(_run())
    return TriggerResponse(run_id=run.id)


# Workstream E3: unified run trigger
@router.post("/runs")
async def trigger_run(body: TriggerRunRequest, request: Request, _admin=Depends(require_api_key_admin)):
    """Trigger a discovery or health-check run via API (E3).

    Returns immediately with the run_id. The run executes in the background.
    Returns 409 Conflict if a run is already in progress for the resource
    (engine's per-resource lock).
    """
    store = _get_store(request)
    engine = _get_engine(request)

    resource = store.get_resource(body.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    if body.run_type not in ("discovery", "health_check"):
        raise HTTPException(status_code=400, detail="run_type must be 'discovery' or 'health_check'")

    # Check for existing running run
    runs = store.get_runs(body.resource_id, limit=1)
    if runs and str(runs[0].status) in ("running", "pending"):
        raise HTTPException(status_code=409, detail="A run is already in progress for this resource")

    from ..models import Run, RunStatus

    rt = RunType.DISCOVERY if body.run_type == "discovery" else RunType.HEALTH_CHECK
    run = Run(resource_id=body.resource_id, run_type=rt, status=RunStatus.PENDING)
    store.save_run(run)

    async def _run():
        try:
            if rt == RunType.DISCOVERY:
                await engine.run_discovery_async(body.resource_id)
            else:
                await engine.run_health_check_async(body.resource_id)
        except Exception as e:
            logger.error("Background run failed: resource=%s type=%s error=%s", body.resource_id, body.run_type, e)

    asyncio.create_task(_run())
    return TriggerResponse(run_id=run.id)


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
            incidents.append({
                "timestamp": str(ev.created_at),
                "from_severity": prev_severity,
                "to_severity": str(ev.severity),
                "summary": ev.summary,
                "correlation": ev.correlation,
            })
        prev_severity = str(ev.severity)

    # Most recent first
    incidents.reverse()
    return {"ok": True, "resource_id": resource_id, "incidents": incidents[:limit]}


