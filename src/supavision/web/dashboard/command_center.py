"""Command Center — structured query interface for system data."""

from __future__ import annotations

import html as html_mod
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ...models import RunType
from . import _render, _require_admin

router = APIRouter()


def _severity_class(sev: str) -> str:
    """Map severity string to a CSS badge class."""
    mapping = {
        "critical": "critical",
        "high": "critical",
        "warning": "warning",
        "medium": "warning",
        "low": "info",
        "info": "info",
        "healthy": "healthy",
    }
    return mapping.get(str(sev).lower(), "unknown")


@router.get("/command-center", response_class=HTMLResponse)
async def command_center_page(request: Request):
    """Render the command center page with resource list for dropdowns."""
    store = request.app.state.store
    resources = store.list_resources()
    severities = ["critical", "high", "medium", "low", "info"]
    return _render(request, "command_center.html", {
        "resources": resources,
        "severities": severities,
    })


@router.post("/command-center/query", response_class=HTMLResponse)
async def command_center_query(
    request: Request,
    command: str = Form(...),
    resource_id: str = Form(""),
    severity: str = Form(""),
):
    """Execute a structured command and return an HTMX fragment."""
    _require_admin(request)
    store = request.app.state.store
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if command == "system_overview":
        result = _cmd_system_overview(store, now)
    elif command == "resource_health":
        result = _cmd_resource_health(store, resource_id, now)
    elif command == "baseline_diff":
        result = _cmd_baseline_diff(store, resource_id, now)
    elif command == "project_stats":
        result = _cmd_project_stats(store, now)
    else:
        result = {
            "title": "Unknown Command",
            "html": f'<p class="text-muted">Unknown command: {html_mod.escape(command)}</p>',
            "timestamp": now,
        }

    return _render(request, "_command_result.html", {"result": result})


# ── Command handlers ─────────────────────────────────────────────


def _cmd_system_overview(store, now: str) -> dict:
    resources = store.list_resources()
    evals = store.get_latest_evaluations_batch()

    severity_counts: dict[str, int] = {}
    for r in resources:
        ev = evals.get(r.id)
        sev = str(ev.severity) if ev else "unknown"
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    total = len(resources)
    critical = severity_counts.get("critical", 0)
    warning = severity_counts.get("warning", 0)
    healthy = severity_counts.get("healthy", 0)

    if critical > 0 and critical == total:
        status_text, status_cls = "Major Outage", "critical"
    elif critical > 0:
        status_text, status_cls = "Partial Outage", "critical"
    elif warning > 0:
        status_text, status_cls = "Degraded Performance", "warning"
    elif healthy > 0:
        status_text, status_cls = "All Systems Operational", "healthy"
    else:
        status_text, status_cls = "Awaiting Data", "unknown"

    rows = ""
    for sev, count in sorted(severity_counts.items(), key=lambda x: x[1], reverse=True):
        badge = f'<span class="badge badge--{_severity_class(sev)}">{html_mod.escape(sev)}</span>'
        rows += (
            f'<div class="stat-card"><span class="stat-value">{count}</span>'
            f'<span class="stat-label">{badge}</span></div>'
        )

    html = (
        f'<div class="cc-status-badge cc-status-badge--{status_cls} mb-4">'
        f'<span class="status-banner-dot"></span> {status_text}</div>'
        f'<div class="stat-grid">'
        f'<div class="stat-card"><span class="stat-value">{total}</span>'
        f'<span class="stat-label">Total Resources</span></div>'
        f'{rows}</div>'
    )
    return {"title": "System Overview", "html": html, "timestamp": now}


def _cmd_resource_health(store, resource_id: str, now: str) -> dict:
    if not resource_id:
        return {
            "title": "Resource Health",
            "html": '<p class="text-muted">Please select a resource.</p>',
            "timestamp": now,
        }

    resource = store.get_resource(resource_id)
    if not resource:
        return {
            "title": "Resource Health",
            "html": '<p class="text-muted">Resource not found.</p>',
            "timestamp": now,
        }

    # Latest reports
    health_reports = store.get_recent_reports(resource_id, RunType.HEALTH_CHECK, limit=1)
    discovery_reports = store.get_recent_reports(resource_id, RunType.DISCOVERY, limit=1)

    # Latest evaluation
    evaluations = store.get_recent_evaluations(resource_id, limit=1)

    # Metrics
    metrics = store.get_latest_metrics(resource_id)

    parts = [f'<h4>{html_mod.escape(resource.name)}</h4>']

    # Evaluation summary
    if evaluations:
        ev = evaluations[0]
        sev = str(ev.severity)
        parts.append(
            f'<div class="mb-3">'
            f'<span class="badge badge--{_severity_class(sev)}">{html_mod.escape(sev)}</span> '
            f'{html_mod.escape(ev.summary or "No summary")}</div>'
        )
    else:
        parts.append('<p class="text-muted">No evaluations yet.</p>')

    # Metrics
    if metrics:
        metric_cards = "".join(
            f'<div class="stat-card"><span class="stat-value">{v:.1f}</span>'
            f'<span class="stat-label">{html_mod.escape(k)}</span></div>'
            for k, v in metrics.items()
        )
        parts.append(f'<div class="stat-grid">{metric_cards}</div>')

    # Report info
    if health_reports:
        rpt = health_reports[0]
        parts.append(
            f'<p class="text-sm text-muted mt-3">'
            f'Last health check: {html_mod.escape(str(rpt.created_at))}</p>'
        )
    if discovery_reports:
        rpt = discovery_reports[0]
        parts.append(
            f'<p class="text-sm text-muted">'
            f'Last discovery: {html_mod.escape(str(rpt.created_at))}</p>'
        )

    return {"title": "Resource Health", "html": "\n".join(parts), "timestamp": now}


def _cmd_baseline_diff(store, resource_id: str, now: str) -> dict:
    if not resource_id:
        return {
            "title": "Baseline Comparison",
            "html": '<p class="text-muted">Please select a resource.</p>',
            "timestamp": now,
        }

    resource = store.get_resource(resource_id)
    if not resource:
        return {
            "title": "Baseline Comparison",
            "html": '<p class="text-muted">Resource not found.</p>',
            "timestamp": now,
        }

    context = store.get_latest_context(resource_id)
    history = store.get_context_history(resource_id, limit=2)

    if not context:
        return {
            "title": "Baseline Comparison",
            "html": f'<p class="text-muted">No baseline found for {html_mod.escape(resource.name)}.</p>',
            "timestamp": now,
        }

    parts = [f'<h4>{html_mod.escape(resource.name)} — Baseline</h4>']
    parts.append(
        f'<div class="stat-grid">'
        f'<div class="stat-card"><span class="stat-value">v{context.version}</span>'
        f'<span class="stat-label">Version</span></div>'
        f'<div class="stat-card"><span class="stat-value">{html_mod.escape(str(context.created_at)[:10])}</span>'
        f'<span class="stat-label">Created</span></div>'
        f'</div>'
    )

    # Show first 200 chars of content as preview
    preview = (context.content or "")[:200]
    if preview:
        ellipsis = "..." if len(context.content or "") > 200 else ""
        parts.append(
            f'<p class="text-sm mt-3">{html_mod.escape(preview)}{ellipsis}</p>'
        )

    if len(history) > 1:
        prev = history[1]
        parts.append(
            f'<p class="text-sm text-muted mt-2">'
            f'Previous version: v{prev.version} ({html_mod.escape(str(prev.created_at)[:10])})</p>'
        )

    return {"title": "Baseline Comparison", "html": "\n".join(parts), "timestamp": now}


def _cmd_project_stats(store, now: str) -> dict:
    resources = store.list_resources()
    total_resources = len(resources)

    html = (
        f'<div class="stat-grid">'
        f'<div class="stat-card"><span class="stat-value">{total_resources}</span>'
        f'<span class="stat-label">Resources</span></div>'
        f'</div>'
    )

    return {"title": "Project Stats", "html": html, "timestamp": now}
