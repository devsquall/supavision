"""REST API routes for Supervisor."""

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
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


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
    return request.app.state.engine


# ── Health ──────────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "service": "supervisor"}


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

    return {
        "ok": True,
        "resource": resource.model_dump(mode="json"),
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
        content="Test notification from Supervisor API.",
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
