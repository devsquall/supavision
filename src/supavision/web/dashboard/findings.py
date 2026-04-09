"""Findings — diagnostic insights list, detail, evaluation, and job streaming."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...models import FindingStage
from . import _check_rate_limit, _render

router = APIRouter()


@router.get("/findings", response_class=HTMLResponse)
async def findings_page(
    request: Request,
    resource_id: str = "",
    stage: str = "",
    severity: str = "",
    page: int = 1,
):
    store = request.app.state.store

    items, total = store.list_work_items(
        resource_id=resource_id or None,
        stage=stage or None,
        severity=severity or None,
        page=max(1, page),
        per_page=50,
    )

    stage_action_map = {
        "created": ("Investigate", "Detected \u2014 needs AI analysis"),
        "scanned": ("Investigate", "Detected \u2014 needs AI analysis"),
        "evaluated": ("View Details", "AI assessment complete"),
        "dismissed": ("Acknowledged", "Acknowledged and closed"),
        # Legacy stages — render as terminal
        "approved": ("View Details", "Legacy"),
        "implementing": ("View Details", "Legacy"),
        "completed": ("View Details", "Legacy"),
        "rejected": ("Acknowledged", "Legacy"),
    }
    item_data = []
    for item in items:
        stage_val = item.stage.value
        action_label, stage_guidance = stage_action_map.get(
            stage_val, ("View Details", "")
        )
        explanation = ""
        if hasattr(item, "evaluation_reasoning") and item.evaluation_reasoning:
            explanation = item.evaluation_reasoning[:120]
        elif hasattr(item, "category"):
            explanation = (
                item.category.replace("-", " ").replace("_", " ").title()
            )
        elif hasattr(item, "description") and item.description:
            explanation = item.description[:120]

        recommendation = ""
        if (
            hasattr(item, "evaluation_fix_approach")
            and item.evaluation_fix_approach
        ):
            recommendation = item.evaluation_fix_approach[:120]

        confidence = getattr(item, "confidence", 0.0)

        item_data.append({
            "id": item.id,
            "severity": item.severity.value,
            "title": item.display_title,
            "file_path": item.file_path if hasattr(item, "file_path") else "",
            "stage": stage_val,
            "source": item.source.value,
            "action_label": action_label,
            "stage_guidance": stage_guidance,
            "explanation": explanation,
            "recommendation": recommendation,
            "confidence": confidence,
        })

    stage_counts = store.count_work_items_by_stage(
        resource_id=resource_id or None
    )

    # Only show active stages in filter dropdown
    active_stages = ["created", "scanned", "evaluated", "dismissed"]
    severities = ["critical", "high", "medium", "low", "info"]

    # Find the most urgent actionable finding for "Next action" prompt
    action_priority = ["evaluated", "scanned", "created"]
    next_action_item = None
    for target_stage in action_priority:
        for d in item_data:
            if d["stage"] == target_stage:
                next_action_item = d
                break
        if next_action_item:
            break

    return _render(request, "findings.html", {
        "items": item_data,
        "total": total,
        "page": max(1, page),
        "per_page": 50,
        "stage_counts": stage_counts,
        "stages": active_stages,
        "severities": severities,
        "next_action_item": next_action_item,
        "filters": {
            "resource_id": resource_id,
            "stage": stage,
            "severity": severity,
        },
    })


@router.get("/findings/{item_id}", response_class=HTMLResponse)
async def finding_detail(item_id: str, request: Request):
    store = request.app.state.store
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")

    jobs = store.list_agent_jobs(work_item_id=item_id)
    transitions = store.list_transitions(item_id)

    # Check for active (running or pending) job
    active_job = None
    for job in jobs:
        if job.status.value in ("running", "pending"):
            active_job = job
            break

    # Find most recent completed job with output (for replay)
    last_completed_job = None
    for job in jobs:
        if job.status.value in ("completed", "failed") and job.output:
            last_completed_job = job
            break

    jobs_data = []
    for job in jobs:
        duration = None
        if job.started_at and job.completed_at:
            delta = job.completed_at - job.started_at
            duration = f"{int(delta.total_seconds())}s"
        jobs_data.append({
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status.value,
            "started_at": (
                job.started_at.strftime("%Y-%m-%d %H:%M")
                if job.started_at else None
            ),
            "duration": duration,
        })

    return _render(request, "finding_detail.html", {
        "item": item,
        "jobs": jobs_data,
        "transitions": transitions,
        "active_job": active_job,
        "last_completed_job": last_completed_job,
    })


@router.post("/findings/{item_id}/evaluate")
async def finding_evaluate(item_id: str, request: Request):
    """Queue AI evaluation — assesses severity and impact."""
    if not _check_rate_limit(request.client.host):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Try again in a minute.",
        )
    from fastapi.responses import RedirectResponse

    from ...codebase_engine import CodebaseEngine

    store = request.app.state.store
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")

    existing = store.list_agent_jobs(work_item_id=item_id)
    active = [j for j in existing if j.status.value in ("pending", "running")]
    if not active:
        engine = CodebaseEngine(store)
        engine.create_evaluate_job(item_id, item.resource_id)

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            content="", status_code=200,
            headers={"HX-Redirect": f"/findings/{item_id}"},
        )
    return RedirectResponse(url=f"/findings/{item_id}", status_code=303)


@router.post("/findings/{item_id}/acknowledge")
async def finding_acknowledge(item_id: str, request: Request):
    """Acknowledge a finding — reviewed, no further action needed."""
    from fastapi.responses import RedirectResponse

    store = request.app.state.store
    item = store.get_work_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Finding not found")
    try:
        item = store.transition_work_item(item_id, FindingStage.DISMISSED)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if request.headers.get("HX-Request"):
        target = request.headers.get("HX-Target", "")
        if "finding-header" in target:
            return HTMLResponse(
                content="", status_code=200,
                headers={"HX-Redirect": f"/findings/{item_id}"},
            )
        return _render(request, "_finding_row.html", {
            "item": _work_item_to_row(item),
        })
    return RedirectResponse(url=f"/findings/{item_id}", status_code=303)


# Backward-compat alias
@router.post("/findings/{item_id}/reject")
async def finding_reject(item_id: str, request: Request):
    """Alias for acknowledge."""
    return await finding_acknowledge(item_id, request)


def _work_item_to_row(item) -> dict:
    return {
        "id": item.id,
        "severity": item.severity.value,
        "title": item.display_title,
        "file_path": item.file_path if hasattr(item, "file_path") else "",
        "stage": item.stage.value,
        "source": item.source.value,
    }


@router.get("/findings/{item_id}/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(item_id: str, job_id: str, request: Request):
    """Agent job transcript viewer."""
    import json as json_mod

    store = request.app.state.store
    job = store.get_agent_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    item = store.get_work_item(item_id)
    output_lines = (job.output or "").split("\n")

    git_diff = ""
    git_data = {}
    diff_lines = []
    if job.result:
        try:
            result_data = json_mod.loads(job.result)
            git_data = result_data.get("git", {})
            git_diff = git_data.get("commit_diff", "")
            if git_diff:
                diff_lines = git_diff.split("\n")
        except (json_mod.JSONDecodeError, TypeError):
            pass

    duration = None
    if job.started_at and job.completed_at:
        delta = job.completed_at - job.started_at
        duration = f"{int(delta.total_seconds())}s"

    return _render(request, "job_detail.html", {
        "job": job,
        "item": item,
        "output_lines": output_lines,
        "git_diff": git_diff,
        "git_data": git_data,
        "diff_lines": diff_lines,
        "duration": duration,
    })


@router.get("/findings/{item_id}/jobs/{job_id}/stream")
async def stream_job_output(item_id: str, job_id: str, request: Request):
    """SSE endpoint — streams live agent output during a job."""
    import json as _json

    from fastapi.responses import StreamingResponse

    from ...agent_runner import get_job_buffer

    async def event_stream():
        cursor = 0
        while True:
            if await request.is_disconnected():
                return
            events, done = get_job_buffer(job_id)
            if cursor < len(events):
                for ts, text in events[cursor:]:
                    msg = _json.dumps({"t": round(ts * 1000), "d": text})
                    yield f"data: {msg}\n\n"
                cursor = len(events)
            if done:
                yield (
                    "event: done\n"
                    f"data: {_json.dumps({'state': 'completed'})}\n\n"
                )
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
