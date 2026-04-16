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

    events = []
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

    events.sort(key=lambda e: e["created_at"], reverse=True)
    events = events[:50]

    return _render(request, "activity.html", {"events": events, "range": range})


def _get_active_items(store) -> list[dict]:
    """Get all currently running/pending runs."""
    resources = {r.id: r.name for r in store.list_resources()}
    items = []
    running_runs, _ = store.list_recent_runs(status="running", limit=20)
    pending_runs, _ = store.list_recent_runs(status="pending", limit=20)
    for run in running_runs + pending_runs:
        elapsed = ""
        if run.started_at:
            delta = datetime.now(timezone.utc) - run.started_at
            elapsed = f"{int(delta.total_seconds())}s"
        items.append({
            "id": run.id, "kind": "run",
            "type": str(run.run_type), "status": str(run.status),
            "resource": resources.get(run.resource_id, run.resource_id[:8]),
            "resource_id": run.resource_id,
            "elapsed": elapsed,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        })
    return items


@router.get("/activity/live", response_class=HTMLResponse)
async def activity_live(request: Request):
    store = request.app.state.store
    active = _get_active_items(store)
    return _render(request, "activity_live.html", {"active_items": active})


@router.get("/api/activity/stream")
async def activity_stream(request: Request):
    """SSE endpoint — streams live output from active runs."""
    from fastapi.responses import StreamingResponse

    from ...engine import get_run_buffer

    store = request.app.state.store

    async def event_stream():
        run_cursors: dict[str, int] = {}
        seen_ids: set[str] = set()
        idle_ticks = 0

        while True:
            if await request.is_disconnected():
                return
            emitted = False
            active = _get_active_items(store)
            active_run_ids = set()

            for item in active:
                if item["kind"] == "run":
                    rid = item["id"]
                    active_run_ids.add(rid)
                    if rid not in run_cursors:
                        run_cursors[rid] = 0
                        if rid not in seen_ids:
                            seen_ids.add(rid)
                            msg = json.dumps({"type": "job_start", "id": rid, "kind": "run",
                                              "job_type": item["type"], "resource": item["resource"],
                                              "status": item["status"]})
                            yield f"data: {msg}\n\n"
                            emitted = True

                    events, done = get_run_buffer(rid)
                    cursor = run_cursors[rid]
                    if cursor < len(events):
                        for ts, text in events[cursor:]:
                            msg = json.dumps({"type": "log", "id": rid, "kind": "run", "text": text})
                            yield f"data: {msg}\n\n"
                            emitted = True
                        run_cursors[rid] = len(events)

                    if done and rid in run_cursors:
                        msg = json.dumps({"type": "job_done", "id": rid, "kind": "run", "status": "completed"})
                        yield f"data: {msg}\n\n"
                        emitted = True
                        del run_cursors[rid]

            for rid in list(run_cursors):
                if rid not in active_run_ids:
                    del run_cursors[rid]

            if not emitted:
                idle_ticks += 1
                if idle_ticks >= 20:
                    msg = json.dumps({"type": "heartbeat", "active": len(active_run_ids)})
                    yield f"data: {msg}\n\n"
                    idle_ticks = 0
            else:
                idle_ticks = 0

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/activity/active")
async def activity_active_json(request: Request):
    store = request.app.state.store
    return {"items": _get_active_items(store)}
