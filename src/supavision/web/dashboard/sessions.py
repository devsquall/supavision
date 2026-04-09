"""Sessions — live and recent agent activity (runs + jobs)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...models import RunStatus, RunType
from ...models.work import JobStatus
from . import _render

router = APIRouter()


def _duration(started_at: datetime | None, completed_at: datetime | None) -> str:
    """Human-readable duration string."""
    if not started_at:
        return "—"
    end = completed_at or datetime.now(timezone.utc)
    delta = end - started_at
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    tab: str = "runs",
    status: str = "",
    run_type: str = "",
    job_type: str = "",
):
    """Lists running + recent infrastructure runs and agent jobs in a tabbed view."""
    store = request.app.state.store

    # Resource name map
    resources = {r.id: r for r in store.list_resources()}

    # Infrastructure Runs
    runs_status = status if tab == "runs" and status else None
    runs_type = run_type if run_type else None
    runs, runs_total = store.list_recent_runs(
        limit=50, offset=0, status=runs_status, run_type=runs_type,
    )

    run_rows = []
    for run in runs:
        res = resources.get(run.resource_id)
        run_rows.append({
            "id": run.id,
            "resource_id": run.resource_id,
            "resource_name": res.name if res else run.resource_id[:8],
            "run_type": str(run.run_type),
            "status": str(run.status),
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "duration": _duration(run.started_at, run.completed_at),
            "tokens": (run.input_tokens or 0) + (run.output_tokens or 0),
            "turns": run.turns,
            "tool_calls": run.tool_calls,
            "error": run.error or "",
        })

    # Agent Jobs
    jobs_status = status if tab == "jobs" and status else None
    jobs_type = job_type if job_type else None
    jobs, jobs_total = store.list_all_agent_jobs(
        limit=50, offset=0, status=jobs_status, job_type=jobs_type,
    )

    job_rows = []
    for job in jobs:
        res = resources.get(job.resource_id)
        work_item_title = ""
        work_item_id = ""
        if not job.work_item_id.startswith("scout-"):
            item = store.get_work_item(job.work_item_id)
            if item:
                work_item_title = item.display_title
                work_item_id = job.work_item_id
        job_rows.append({
            "id": job.id,
            "resource_id": job.resource_id,
            "resource_name": res.name if res else job.resource_id[:8],
            "job_type": job.job_type,
            "status": job.status.value,
            "work_item_title": work_item_title,
            "work_item_id": work_item_id,
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "duration": _duration(job.started_at, job.completed_at),
        })

    return _render(request, "sessions.html", {
        "tab": tab,
        "run_rows": run_rows,
        "runs_total": runs_total,
        "job_rows": job_rows,
        "jobs_total": jobs_total,
        "status_filter": status,
        "run_type_filter": run_type,
        "job_type_filter": job_type,
        "run_statuses": [s.value for s in RunStatus],
        "run_types": [t.value for t in RunType],
        "job_statuses": [s.value for s in JobStatus],
    })


@router.get("/sessions/{session_type}/{session_id}", response_class=HTMLResponse)
async def session_viewer(request: Request, session_type: str, session_id: str):
    """Detail view for a single run or agent job with terminal output."""
    store = request.app.state.store

    if session_type not in ("run", "job"):
        raise HTTPException(status_code=404, detail="Invalid session type")

    resources = {r.id: r for r in store.list_resources()}

    if session_type == "run":
        run = store.get_run(session_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        res = resources.get(run.resource_id)
        return _render(request, "session_viewer.html", {
            "session_type": "run",
            "session_id": run.id,
            "resource_id": run.resource_id,
            "resource_name": res.name if res else run.resource_id[:8],
            "type_label": str(run.run_type),
            "status": str(run.status),
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "duration": _duration(run.started_at, run.completed_at),
            "tokens": (run.input_tokens or 0) + (run.output_tokens or 0),
            "turns": run.turns,
            "tool_calls": run.tool_calls,
            "error": run.error or "",
            "output": run.error or "",
            "is_running": str(run.status) == "running",
            "sse_url": f"/resources/{run.resource_id}/runs/{run.id}/stream",
        })

    # job
    job = store.get_agent_job(session_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    res = resources.get(job.resource_id)
    work_item_title = ""
    work_item_id = ""
    if not job.work_item_id.startswith("scout-"):
        item = store.get_work_item(job.work_item_id)
        if item:
            work_item_title = item.display_title
            work_item_id = job.work_item_id

    return _render(request, "session_viewer.html", {
        "session_type": "job",
        "session_id": job.id,
        "resource_id": job.resource_id,
        "resource_name": res.name if res else job.resource_id[:8],
        "type_label": job.job_type,
        "status": job.status.value,
        "started_at": job.started_at.isoformat() if job.started_at else "",
        "duration": _duration(job.started_at, job.completed_at),
        "tokens": 0,
        "turns": 0,
        "tool_calls": 0,
        "error": job.error or "",
        "output": job.output or "",
        "is_running": job.status.value == "running",
        "work_item_title": work_item_title,
        "work_item_id": work_item_id,
        "sse_url": f"/findings/{job.work_item_id}/jobs/{job.id}/stream",
    })
