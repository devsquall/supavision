"""Web dashboard routes for Supervisor."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..models import RunType

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    store = request.app.state.store
    resources = store.list_resources()
    latest_evals = store.get_latest_evaluations_batch()

    critical = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "critical")
    warning = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "warning")
    healthy = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "healthy")

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_resources": len(resources),
        "critical_count": critical,
        "warning_count": warning,
        "healthy_count": healthy,
    })


@router.get("/dashboard/resources", response_class=HTMLResponse)
async def dashboard_resources(request: Request):
    store = request.app.state.store
    resources = store.list_resources()
    latest_runs = store.get_latest_runs_batch()
    latest_evals = store.get_latest_evaluations_batch()

    resource_data = []
    for r in resources:
        ev = latest_evals.get(r.id)
        run = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        resource_data.append({
            "id": r.id,
            "name": r.name,
            "resource_type": r.resource_type,
            "severity": str(ev.severity) if ev else None,
            "summary": ev.summary if ev else None,
            "last_run_at": run.completed_at.strftime("%Y-%m-%d %H:%M") if run and run.completed_at else None,
        })

    # Sort: critical first, then warning, then healthy, then unknown
    order = {"critical": 0, "warning": 1, "healthy": 2, None: 3}
    resource_data.sort(key=lambda x: order.get(x["severity"], 3))

    return templates.TemplateResponse(request, "resource_list.html", {
        "resources": resource_data,
    })


@router.get("/resources", response_class=HTMLResponse)
async def resources_page(request: Request):
    """Redirect /resources to dashboard."""
    return await dashboard(request)


@router.get("/resources/{resource_id}", response_class=HTMLResponse)
async def resource_detail(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    context = store.get_latest_context(resource_id)
    checklist = store.get_latest_checklist(resource_id)
    recent_runs = store.get_runs(resource_id, limit=10)

    # Attach severity to runs
    runs_data = []
    for run in recent_runs:
        severity = None
        if run.evaluation_id:
            ev = store.get_evaluation(run.evaluation_id)
            if ev:
                severity = str(ev.severity)
        duration = None
        if run.started_at and run.completed_at:
            delta = run.completed_at - run.started_at
            duration = f"{int(delta.total_seconds())}s"
        runs_data.append({
            "run_type": str(run.run_type),
            "status": str(run.status),
            "severity": severity,
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "-",
            "duration": duration,
            "report_id": run.report_id,
        })

    latest_eval = store.get_recent_evaluations(resource_id, limit=1)
    severity = str(latest_eval[0].severity) if latest_eval else None

    return templates.TemplateResponse(request, "resource_detail.html", {
        "resource": resource,
        "context": context,
        "checklist": checklist,
        "runs": runs_data,
        "severity": severity,
    })


@router.post("/resources/{resource_id}/discover")
async def trigger_discover(resource_id: str, request: Request):
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    async def _run():
        try:
            await engine.run_discovery_async(resource_id)
        except Exception as e:
            logger.error("Dashboard discovery failed for %s: %s", resource_id, e)

    asyncio.create_task(_run())
    return Response(status_code=204)


@router.post("/resources/{resource_id}/health-check")
async def trigger_health_check(resource_id: str, request: Request):
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    async def _run():
        try:
            await engine.run_health_check_async(resource_id)
        except Exception as e:
            logger.error("Dashboard health check failed for %s: %s", resource_id, e)

    asyncio.create_task(_run())
    return Response(status_code=204)


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(report_id: str, request: Request):
    store = request.app.state.store
    report = store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    evaluation = None
    evals = store.get_recent_evaluations(report.resource_id, limit=20)
    for ev in evals:
        if ev.report_id == report_id:
            evaluation = ev
            break

    resource = store.get_resource(report.resource_id)
    resource_name = resource.name if resource else report.resource_id

    return templates.TemplateResponse(request, "report_detail.html", {
        "report": report,
        "evaluation": evaluation,
        "resource_name": resource_name,
    })
