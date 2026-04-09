"""Reports listing — health check and discovery reports across all resources."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import _render

router = APIRouter()


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    resource_id: str = "",
    run_type: str = "",
    page: int = 1,
):
    store = request.app.state.store
    per_page = 50
    page = max(1, page)
    offset = (page - 1) * per_page

    resources = store.list_resources()

    items, total = store.list_all_reports(
        limit=per_page,
        offset=offset,
        resource_id=resource_id or None,
        run_type=run_type or None,
    )

    return _render(request, "reports.html", {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "resources": resources,
        "run_types": ["discovery", "health_check"],
        "filters": {
            "resource_id": resource_id,
            "run_type": run_type,
        },
    })
