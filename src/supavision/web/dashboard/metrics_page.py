"""Metrics — resource health metrics and codebase statistics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import _render

router = APIRouter()


def _compute_sparkline(history: list[dict], width: int = 120, height: int = 32) -> str:
    """Compute SVG polyline points from metric history."""
    if not history or len(history) < 2:
        return ""
    values = [h["value"] for h in history]
    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v if max_v != min_v else 1.0
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = round(i / (n - 1) * width, 1)
        y = round(height - ((v - min_v) / span) * height, 1)
        points.append(f"{x},{y}")
    return " ".join(points)


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request):
    """Resource health metrics and codebase statistics."""
    store = request.app.state.store
    resources = store.list_resources()

    infra_cards = []
    codebase_cards = []

    for resource in resources:
        if resource.resource_type == "codebase":
            # Codebase: work item counts by stage
            stage_counts = store.count_work_items_by_stage(resource_id=resource.id)
            if stage_counts:
                total = sum(stage_counts.values())
                codebase_cards.append({
                    "resource_name": resource.name,
                    "resource_id": resource.id,
                    "stage_counts": stage_counts,
                    "total": total,
                })
        else:
            # Infrastructure: health metrics
            latest = store.get_latest_metrics(resource.id)
            if not latest:
                continue
            metrics = []
            for name, value in sorted(latest.items()):
                history = store.get_metrics_history(resource.id, name, days=30)
                sparkline_points = _compute_sparkline(history)
                # Determine unit heuristic
                unit = ""
                name_lower = name.lower()
                if "percent" in name_lower or "cpu" in name_lower or "memory" in name_lower:
                    unit = "%"
                elif "bytes" in name_lower:
                    unit = "B"
                elif "ms" in name_lower or "latency" in name_lower:
                    unit = "ms"
                elif "count" in name_lower or "total" in name_lower:
                    unit = ""

                metrics.append({
                    "name": name,
                    "value": value,
                    "display_value": f"{value:,.1f}" if isinstance(value, float) else str(value),
                    "unit": unit,
                    "sparkline_points": sparkline_points,
                    "sparkline_width": 120,
                    "sparkline_height": 32,
                })
            infra_cards.append({
                "resource_name": resource.name,
                "resource_id": resource.id,
                "metrics": metrics,
            })

    return _render(request, "metrics.html", {
        "infra_cards": infra_cards,
        "codebase_cards": codebase_cards,
    })
