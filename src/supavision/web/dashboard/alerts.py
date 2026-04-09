"""Alerts listing — notification history and delivery status."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import _render

router = APIRouter()


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    resource_id: str = "",
    severity: str = "",
    channel: str = "",
    status: str = "",
    page: int = 1,
):
    store = request.app.state.store
    per_page = 50
    page = max(1, page)
    offset = (page - 1) * per_page

    resources = store.list_resources()
    resource_map = {r.id: r.name for r in resources}

    items, total = store.list_notifications_extended(
        limit=per_page,
        offset=offset,
        resource_id=resource_id or None,
        severity=severity or None,
        channel=channel or None,
        status=status or None,
    )

    # Attach resource names to each notification
    for item in items:
        item["resource_name"] = resource_map.get(item.get("resource_id"), "Unknown")

    return _render(request, "alerts.html", {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "resources": resources,
        "severities": ["critical", "warning", "healthy"],
        "channels": ["slack", "webhook"],
        "statuses": ["sent", "failed"],
        "filters": {
            "resource_id": resource_id,
            "severity": severity,
            "channel": channel,
            "status": status,
        },
    })
