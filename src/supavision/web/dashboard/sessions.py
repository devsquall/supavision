"""Sessions — live and recent infrastructure runs."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...models import RunStatus, RunType
from . import _render

router = APIRouter()


def _duration(started_at: datetime | None, completed_at: datetime | None) -> str:
    """Human-readable duration string."""
    if not started_at:
        return "\u2014"
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
    status: str = "",
    run_type: str = "",
):
    """Lists running + recent infrastructure runs."""
    store = request.app.state.store

    # Resource name map
    resources = {r.id: r for r in store.list_resources()}

    # Infrastructure Runs
    runs_status = status or None
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

    return _render(request, "sessions.html", {
        "run_rows": run_rows,
        "runs_total": runs_total,
        "status_filter": status,
        "run_type_filter": run_type,
        "run_statuses": [s.value for s in RunStatus],
        "run_types": [t.value for t in RunType],
    })


@router.get("/sessions/{session_type}/{session_id}", response_class=HTMLResponse)
async def session_viewer(request: Request, session_type: str, session_id: str):
    """Detail view for a single run with terminal output."""
    store = request.app.state.store

    if session_type != "run":
        raise HTTPException(status_code=404, detail="Invalid session type")

    resources = {r.id: r for r in store.list_resources()}

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
