"""REST API routes for Supavision."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..models import (
    Resource,
    RunType,
    Severity,
)
from .auth import require_api_key

logger = logging.getLogger(__name__)

# Health endpoint — no auth required (for Docker healthcheck, load balancers, uptime monitors)
health_router = APIRouter(prefix="/api/v1")


@health_router.get("/health")
async def health():
    return {"status": "ok", "service": "supavision"}


# All other API routes require API key auth
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/system/status")
async def system_status(request: Request):
    from ..scheduler import get_scheduler_status
    from ..agent_runner import get_runner
    from .. import __version__

    runner = get_runner()
    return {
        "ok": True,
        "version": __version__,
        "scheduler": get_scheduler_status(),
        "agent_runner": runner.get_status() if runner else {"running": False, "pending_jobs": 0},
    }


# ── Request/Response models ─────────────────────────────────────


class CreateResourceRequest(BaseModel):
    name: str
    resource_type: str
    parent_id: str = ""
    config: dict[str, str] = {}


class TriggerResponse(BaseModel):
    ok: bool = True
    run_id: str


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
async def list_resources(request: Request):
    store = _get_store(request)
    resources = store.list_resources()
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

    return {"ok": True, "resources": result}


@router.post("/resources")
async def create_resource(body: CreateResourceRequest, request: Request):
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
async def delete_resource(resource_id: str, request: Request):
    store = _get_store(request)
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    store.delete_resource(resource_id)
    return {"ok": True, "deleted": resource_id}


# ── Trigger Runs ────────────────────────────────────────────────


@router.post("/resources/{resource_id}/discover")
async def trigger_discovery(resource_id: str, request: Request):
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
async def trigger_health_check(resource_id: str, request: Request):
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
):
    store = _get_store(request)
    if not resource_id:
        raise HTTPException(status_code=400, detail="resource_id query parameter required")

    reports = store.get_recent_reports(resource_id, RunType(run_type), limit=limit)
    return {
        "ok": True,
        "reports": [r.model_dump(mode="json") for r in reports],
    }


# ── Notifications ───────────────────────────────────────────────


@router.post("/resources/{resource_id}/notify-test")
async def notify_test(resource_id: str, request: Request):
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


# ── Codebase endpoints ──────────────────────────────────────────


@router.post("/codebase/{resource_id}/scan")
async def codebase_scan(resource_id: str, request: Request):
    """Run a regex scan on a codebase resource."""
    from ..codebase_engine import CodebaseEngine

    store = request.app.state.store
    engine = CodebaseEngine(store)
    try:
        run = engine.run_scan(resource_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    items, total = store.list_work_items(
        resource_id=resource_id, run_id=run.id
    )
    return {
        "ok": True,
        "run_id": run.id,
        "findings_created": total,
        "report_id": run.report_id,
    }


# ── Findings endpoints ─────────────────────────────────────────


@router.get("/findings")
async def list_findings(
    request: Request,
    resource_id: str = "",
    stage: str = "",
    severity: str = "",
    page: int = 1,
    limit: int = 50,
):
    """List codebase findings with optional filters."""
    store = _get_store(request)
    items, total = store.list_work_items(
        resource_id=resource_id or None,
        stage=stage or None,
        severity=severity or None,
        page=page,
        per_page=min(limit, 100),
    )
    return {
        "ok": True,
        "total": total,
        "page": page,
        "items": [
            {
                "id": item.id,
                "resource_id": item.resource_id,
                "stage": item.stage.value,
                "severity": item.severity.value,
                "title": item.display_title,
                "source": item.source.value,
                "created_at": str(item.created_at),
            }
            for item in items
        ],
    }


@router.get("/findings/{item_id}")
async def get_finding(item_id: str, request: Request):
    """Get full details of a finding/work item."""
    store = _get_store(request)
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")
    jobs = store.list_agent_jobs(work_item_id=item_id)
    return {
        "ok": True,
        "finding": item.model_dump(mode="json"),
        "jobs": [j.model_dump(mode="json") for j in jobs],
    }


@router.post("/findings/{item_id}/evaluate")
async def evaluate_finding(item_id: str, request: Request):
    """Queue an evaluation job for a finding."""
    from ..codebase_engine import CodebaseEngine

    store = _get_store(request)
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")
    engine = CodebaseEngine(store)
    job = engine.create_evaluate_job(item_id, item.resource_id)
    return {"ok": True, "job_id": job.id, "status": job.status.value}


@router.post("/findings/{item_id}/approve")
async def approve_finding(item_id: str, request: Request):
    """Approve a finding for implementation."""
    from ..models import FindingStage

    store = _get_store(request)
    item = store.transition_work_item(item_id, FindingStage.APPROVED)
    return {"ok": True, "id": item.id, "stage": item.stage.value}


@router.post("/findings/{item_id}/reject")
async def reject_finding(item_id: str, request: Request):
    """Reject a finding."""
    from ..models import FindingStage

    store = _get_store(request)
    item = store.transition_work_item(item_id, FindingStage.REJECTED)
    return {"ok": True, "id": item.id, "stage": item.stage.value}


@router.post("/findings/{item_id}/implement")
async def implement_finding(item_id: str, request: Request):
    """Queue an implementation job for an approved finding."""
    from ..codebase_engine import CodebaseEngine

    store = _get_store(request)
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")
    try:
        engine = CodebaseEngine(store)
        job = engine.create_implement_job(item_id, item.resource_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job_id": job.id, "status": job.status.value}


@router.post("/resources/{resource_id}/scout")
async def scout_resource(
    resource_id: str, request: Request, focus: str = "general",
):
    """Launch a scout agent to explore a codebase resource."""
    from ..codebase_engine import CodebaseEngine

    store = _get_store(request)
    try:
        engine = CodebaseEngine(store)
        job = engine.create_scout_job(resource_id, focus)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job_id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Get agent job status and output."""
    store = _get_store(request)
    job = store.get_agent_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": job.model_dump(mode="json")}


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


@router.get("/blocklist")
async def list_blocklist(request: Request):
    """List known false-positive blocklist entries."""
    store = _get_store(request)
    entries = store.list_blocklist()
    return {
        "ok": True,
        "total": len(entries),
        "entries": [
            {
                "id": e.id,
                "pattern_signature": e.pattern_signature,
                "category": e.category,
                "description": e.description,
            }
            for e in entries
        ],
    }
