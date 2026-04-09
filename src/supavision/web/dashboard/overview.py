"""Dashboard overview routes — home page, stats, live activity."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...models import JobStatus, RunType
from . import _render

router = APIRouter()


def _compute_sort_score(severity: str, last_check_iso: str | None, stage: str | None) -> float:
    """Prioritize action items by severity, staleness, and pipeline stage."""
    severity_weights = {
        "critical": 0, "high": 10, "warning": 20, "medium": 30, "low": 40, "info": 50,
    }
    score = severity_weights.get(severity, 50)

    # Staleness bonus — older unchecked items are more urgent
    if last_check_iso:
        try:
            last_dt = datetime.fromisoformat(last_check_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            hours_ago = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if hours_ago > 72:
                score -= 10
            elif hours_ago > 24:
                score -= 5
        except (ValueError, TypeError):
            pass

    # Stage readiness — items blocking the pipeline are more urgent
    stage_bonuses = {"evaluated": -3, "approved": -2, "scanned": 0}
    if stage:
        score += stage_bonuses.get(stage, 0)

    return score


def _get_recent_events(store, limit: int = 10) -> list[dict]:
    """Get recent events across all resources using batch queries."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    resources = {r.id: r.name for r in store.list_resources()}
    events = []

    # Recent runs (global query, no per-resource iteration)
    for run in store.get_recent_runs_global(limit=20, since=cutoff):
        ev = store.get_evaluation(run.evaluation_id) if run.evaluation_id else None
        severity = str(ev.severity) if ev else None
        summary = ev.summary[:80] if ev and ev.summary else str(run.status)
        events.append({
            "type": str(run.run_type),
            "resource_name": resources.get(run.resource_id, "Unknown"),
            "severity": severity,
            "summary": summary,
            "created_at": (run.started_at or run.created_at).isoformat(),
            "link": f"/resources/{run.resource_id}",
        })

    # Recent agent jobs
    for job in store.list_agent_jobs(limit=20):
        try:
            job_created = job.created_at.isoformat() if job.created_at else ""
        except (AttributeError, TypeError):
            job_created = ""
        if job_created < cutoff:
            continue
        is_scout = job.work_item_id.startswith("scout-")
        item = store.get_work_item(job.work_item_id) if not is_scout else None
        title = item.display_title if item else job.work_item_id
        sev_map = {"completed": "info", "running": "warning", "failed": "critical"}
        events.append({
            "type": job.job_type,
            "resource_name": resources.get(job.resource_id, "Unknown"),
            "severity": sev_map.get(job.status.value),
            "summary": f"{title} — {job.status.value}",
            "created_at": job_created,
            "link": f"/findings/{job.work_item_id}" if item else f"/resources/{job.resource_id}",
        })

    events.sort(key=lambda e: e["created_at"], reverse=True)
    return events[:limit]


def _get_live_activities(store) -> list[dict]:
    """Get currently running/pending runs and agent jobs."""
    resources = {r.id: r.name for r in store.list_resources()}
    activities = []

    for run in store.get_pending_runs() + store.get_running_runs():
        activities.append({
            "type": str(run.run_type),
            "label": resources.get(run.resource_id, "Unknown"),
            "status": str(run.status),
            "started_at": (run.started_at or run.created_at).isoformat(),
            "link": f"/resources/{run.resource_id}",
        })

    for job_status, job_limit in [(JobStatus.RUNNING, 10), (JobStatus.PENDING, 5)]:
        for job in store.list_agent_jobs(status=str(job_status), limit=job_limit):
            is_scout = job.work_item_id.startswith("scout-")
            link = (
                f"/resources/{job.resource_id}" if is_scout
                else f"/findings/{job.work_item_id}"
            )
            activities.append({
                "type": job.job_type,
                "label": resources.get(job.resource_id, "Unknown"),
                "status": str(job.status),
                "started_at": job.created_at.isoformat(),
                "link": link,
            })

    return activities


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _render(request, "dashboard.html")


@router.get("/dashboard/overview", response_class=HTMLResponse)
async def dashboard_overview(request: Request):
    """Control center overview — stats, action items, live activity, recent events."""
    store = request.app.state.store
    resources = store.list_resources()
    latest_evals = store.get_latest_evaluations_batch()
    latest_runs = store.get_latest_runs_batch()
    resource_names = {r.id: r.name for r in resources}

    critical = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "critical")
    warning = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "warning")
    healthy = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "healthy")

    # System status banner
    total = len(resources)
    if critical > 0 and critical == total:
        status_text, status_type = "Major Outage", "critical"
    elif critical > 0:
        status_text, status_type = "Partial Outage", "critical"
    elif warning > 0:
        status_text, status_type = "Degraded Performance", "warning"
    elif healthy > 0:
        status_text, status_type = "All Systems Operational", "healthy"
    else:
        status_text, status_type = "Awaiting Data", "unknown"

    # Build unified action items from urgent resources + actionable findings
    action_items = []

    for r in resources:
        ev = latest_evals.get(r.id)
        sev = str(ev.severity) if ev else None
        if sev not in ("critical", "warning"):
            continue
        run = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        last_check = run.completed_at.isoformat() if run and run.completed_at else None
        explanation = ev.summary[:150] if ev and ev.summary else f"Resource has {sev} status"
        impact = (
            "Investigate and resolve, then re-check." if sev == "critical"
            else "Monitor \u2014 may escalate or self-resolve."
        )
        action_items.append({
            "severity": sev,
            "name": r.name,
            "explanation": explanation,
            "impact": impact,
            "link": f"/resources/{r.id}",
            "action_label": "Investigate",
            "recheck_url": f"/resources/{r.id}/health-check",
            "sort_score": _compute_sort_score(sev, last_check, None),
            "last_check": last_check,
        })

    for item in store.get_actionable_work_items(limit=10):
        stage = item.stage.value
        label_map = {"evaluated": "View Details", "scanned": "Investigate"}
        impact_map = {
            "evaluated": "AI assessment complete \u2014 review the analysis.",
            "scanned": "Detected by scanner \u2014 needs AI analysis.",
        }
        action_items.append({
            "severity": item.severity.value,
            "name": item.display_title,
            "explanation": f"Finding in {resource_names.get(item.resource_id, 'Unknown')}",
            "impact": impact_map.get(stage, ""),
            "link": f"/findings/{item.id}",
            "action_label": label_map.get(stage, "Investigate"),
            "recheck_url": None,
            "sort_score": _compute_sort_score(item.severity.value, None, stage),
            "last_check": None,
        })

    action_items.sort(key=lambda x: (x["sort_score"], x.get("last_check") or ""))
    total_action_items = len(action_items)
    action_items = action_items[:8]

    return _render(request, "dashboard_overview.html", {
        "total_resources": total,
        "critical_count": critical,
        "warning_count": warning,
        "healthy_count": healthy,
        "system_status_text": status_text,
        "system_status_type": status_type,
        "action_items": action_items,
        "total_action_items": total_action_items,
        "recent_events": _get_recent_events(store, limit=10),
        "activities": _get_live_activities(store),
        "has_resources": total > 0,
    })


@router.get("/dashboard/live-activity", response_class=HTMLResponse)
async def dashboard_live_activity(request: Request):
    """Live activity fragment — running/pending runs + agent jobs (polled every 5s)."""
    store = request.app.state.store
    return _render(request, "dashboard_live_activity.html", {
        "activities": _get_live_activities(store),
    })
