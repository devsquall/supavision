"""Activity timeline — global event feed across all resources."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import _render

router = APIRouter()

@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, range: str = "24h"):
    """Global timeline of all events across all resources."""
    store = request.app.state.store
    from datetime import timedelta

    hours = {"24h": 24, "7d": 168, "30d": 720}.get(range, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Gather events from multiple sources
    events = []

    # Runs (health checks, discoveries, scans)
    resources = {r.id: r for r in store.list_resources()}
    for resource in resources.values():
        for run in store.get_runs(resource.id, limit=20):
            if run.started_at and run.started_at < cutoff:
                continue
            ev = store.get_evaluation(run.evaluation_id) if run.evaluation_id else None
            severity = str(ev.severity) if ev else "unknown"
            summary = ev.summary[:80] if ev else str(run.status)
            events.append({
                "type": str(run.run_type),
                "resource_name": resource.name,
                "severity": severity if severity != "unknown" else None,
                "summary": summary,
                "created_at": (run.started_at or run.created_at).isoformat(),
                "link": f"/resources/{resource.id}",
            })

    # Sort by time descending
    events.sort(key=lambda e: e["created_at"], reverse=True)
    events = events[:50]  # Cap at 50

    return _render(request, "activity.html", {
        "events": events,
        "range": range,
    })


# ── Live Activity ────────────────────────────────────────────────


def _get_active_items(store) -> list[dict]:
    """Get all currently running/pending runs and jobs."""
    resources = {r.id: r.name for r in store.list_resources()}
    items = []

    # Active runs (health checks, discoveries, scans)
    running_runs, _ = store.list_recent_runs(status="running", limit=20)
    pending_runs, _ = store.list_recent_runs(status="pending", limit=20)
    for run in running_runs + pending_runs:
        elapsed = ""
        if run.started_at:
            delta = datetime.now(timezone.utc) - run.started_at
            elapsed = f"{int(delta.total_seconds())}s"
        items.append({
            "id": run.id,
            "kind": "run",
            "type": str(run.run_type),
            "status": str(run.status),
            "resource": resources.get(run.resource_id, run.resource_id[:8]),
            "resource_id": run.resource_id,
            "elapsed": elapsed,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        })

    # Active agent jobs (evaluate, implement, scout)
    running_jobs, _ = store.list_all_agent_jobs(status="running", limit=20)
    pending_jobs, _ = store.list_all_agent_jobs(status="pending", limit=20)
    for job in running_jobs + pending_jobs:
        elapsed = ""
        if job.started_at:
            delta = datetime.now(timezone.utc) - job.started_at
            elapsed = f"{int(delta.total_seconds())}s"
        items.append({
            "id": job.id,
            "kind": "job",
            "type": job.job_type,
            "status": job.status.value,
            "resource": resources.get(job.resource_id, job.resource_id[:8] if job.resource_id else "—"),
            "resource_id": job.resource_id,
            "elapsed": elapsed,
            "started_at": job.started_at.isoformat() if job.started_at else None,
        })

    return items


@router.get("/activity/live", response_class=HTMLResponse)
async def activity_live(request: Request):
    """Live activity page — real-time jobs and log streaming."""
    store = request.app.state.store
    active = _get_active_items(store)
    return _render(request, "activity_live.html", {"active_items": active})


@router.get("/api/activity/stream")
async def activity_stream(request: Request):
    """SSE endpoint — streams aggregated live output from all active runs and jobs."""
    from fastapi.responses import StreamingResponse

    from ...agent_runner import get_job_buffer
    from ...engine import get_run_buffer

    store = request.app.state.store

    async def event_stream():
        # Track cursors per buffer
        run_cursors: dict[str, int] = {}
        job_cursors: dict[str, int] = {}
        seen_ids: set[str] = set()
        idle_ticks = 0

        while True:
            if await request.is_disconnected():
                return

            emitted = False

            # Discover active items and stream their buffers
            active = _get_active_items(store)
            active_run_ids = set()
            active_job_ids = set()

            for item in active:
                if item["kind"] == "run":
                    rid = item["id"]
                    active_run_ids.add(rid)
                    if rid not in run_cursors:
                        run_cursors[rid] = 0
                        # Announce new run
                        if rid not in seen_ids:
                            seen_ids.add(rid)
                            msg = json.dumps({
                                "type": "job_start",
                                "id": rid,
                                "kind": "run",
                                "job_type": item["type"],
                                "resource": item["resource"],
                                "status": item["status"],
                            })
                            yield f"data: {msg}\n\n"
                            emitted = True

                    events, done = get_run_buffer(rid)
                    cursor = run_cursors[rid]
                    if cursor < len(events):
                        for ts, text in events[cursor:]:
                            msg = json.dumps({
                                "type": "log",
                                "id": rid,
                                "kind": "run",
                                "text": text,
                            })
                            yield f"data: {msg}\n\n"
                            emitted = True
                        run_cursors[rid] = len(events)

                    if done and rid in run_cursors:
                        msg = json.dumps({
                            "type": "job_done",
                            "id": rid,
                            "kind": "run",
                            "status": "completed",
                        })
                        yield f"data: {msg}\n\n"
                        emitted = True
                        del run_cursors[rid]

                elif item["kind"] == "job":
                    jid = item["id"]
                    active_job_ids.add(jid)
                    if jid not in job_cursors:
                        job_cursors[jid] = 0
                        if jid not in seen_ids:
                            seen_ids.add(jid)
                            msg = json.dumps({
                                "type": "job_start",
                                "id": jid,
                                "kind": "job",
                                "job_type": item["type"],
                                "resource": item["resource"],
                                "status": item["status"],
                            })
                            yield f"data: {msg}\n\n"
                            emitted = True

                    events, done = get_job_buffer(jid)
                    cursor = job_cursors[jid]
                    if cursor < len(events):
                        for ts, text in events[cursor:]:
                            msg = json.dumps({
                                "type": "log",
                                "id": jid,
                                "kind": "job",
                                "text": text,
                            })
                            yield f"data: {msg}\n\n"
                            emitted = True
                        job_cursors[jid] = len(events)

                    if done and jid in job_cursors:
                        msg = json.dumps({
                            "type": "job_done",
                            "id": jid,
                            "kind": "job",
                            "status": "completed",
                        })
                        yield f"data: {msg}\n\n"
                        emitted = True
                        del job_cursors[jid]

            # Clean up cursors for items that are no longer active
            for rid in list(run_cursors):
                if rid not in active_run_ids:
                    del run_cursors[rid]
            for jid in list(job_cursors):
                if jid not in active_job_ids:
                    del job_cursors[jid]

            # Send heartbeat every ~10s to keep connection alive
            if not emitted:
                idle_ticks += 1
                if idle_ticks >= 20:  # 20 * 0.5s = 10s
                    active_count = len(active_run_ids) + len(active_job_ids)
                    msg = json.dumps({"type": "heartbeat", "active": active_count})
                    yield f"data: {msg}\n\n"
                    idle_ticks = 0
            else:
                idle_ticks = 0

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/activity/active")
async def activity_active_json(request: Request):
    """JSON endpoint for polling active jobs (fallback when SSE unavailable)."""
    store = request.app.state.store
    return {"items": _get_active_items(store)}
