"""Schedules — automated monitoring schedule management."""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from . import _render, _require_admin

router = APIRouter()


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request):
    """List all resources with their cron schedules."""
    store = request.app.state.store

    from ...scheduler import get_scheduler_status

    scheduler_status = get_scheduler_status()

    resources = store.list_resources()
    schedule_rows = []

    now = datetime.now(timezone.utc)

    for resource in resources:
        has_discovery = resource.discovery_schedule is not None
        has_health = resource.health_check_schedule is not None

        if not has_discovery and not has_health:
            continue

        # Compute next run time from the earliest upcoming schedule
        next_run = None
        discovery_cron = ""
        health_cron = ""

        if has_discovery and resource.discovery_schedule:
            discovery_cron = resource.discovery_schedule.cron
            if resource.enabled and resource.discovery_schedule.enabled:
                try:
                    cron = croniter(discovery_cron, now)
                    candidate = cron.get_next(datetime)
                    if next_run is None or candidate < next_run:
                        next_run = candidate
                except (ValueError, KeyError):
                    pass

        if has_health and resource.health_check_schedule:
            health_cron = resource.health_check_schedule.cron
            if resource.enabled and resource.health_check_schedule.enabled:
                try:
                    cron = croniter(health_cron, now)
                    candidate = cron.get_next(datetime)
                    if next_run is None or candidate < next_run:
                        next_run = candidate
                except (ValueError, KeyError):
                    pass

        schedule_rows.append({
            "resource_id": resource.id,
            "resource_name": resource.name,
            "discovery_cron": discovery_cron,
            "health_cron": health_cron,
            "next_run": next_run.isoformat() if next_run else "",
            "enabled": resource.enabled,
        })

    return _render(request, "schedules.html", {
        "schedule_rows": schedule_rows,
        "scheduler_status": scheduler_status,
    })


@router.post("/schedules/{resource_id}/toggle")
async def toggle_schedule(request: Request, resource_id: str):
    """Toggle a resource's enabled state."""
    _require_admin(request)
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    resource.enabled = not resource.enabled
    resource.updated_at = datetime.now(timezone.utc)
    store.save_resource(resource)

    return Response(status_code=204)
