"""Web dashboard routes for Supervisor."""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..models import RunType
from ..resource_types import RESOURCE_TYPES

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    store = request.app.state.store
    resources = store.list_resources()
    latest_evals = store.get_latest_evaluations_batch()

    critical = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "critical")
    warning = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "warning")
    healthy = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "healthy")

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_resources": len(resources),
        "critical_count": critical,
        "warning_count": warning,
        "healthy_count": healthy,
    })


@router.get("/dashboard/overview", response_class=HTMLResponse)
async def dashboard_overview(request: Request, q: str = ""):
    """Combined stats + resource list fragment for HTMX refresh."""
    store = request.app.state.store
    resources = store.list_resources()
    if q:
        q_lower = q.lower()
        resources = [r for r in resources if q_lower in r.name.lower() or q_lower in r.resource_type.lower()]
    latest_runs = store.get_latest_runs_batch()
    latest_evals = store.get_latest_evaluations_batch()

    resource_data = []
    for r in resources:
        ev = latest_evals.get(r.id)
        run = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        resource_data.append({
            "id": r.id,
            "name": r.name,
            "resource_type": r.resource_type,
            "severity": str(ev.severity) if ev else None,
            "summary": ev.summary if ev else None,
            "last_run_at": run.completed_at.isoformat() if run and run.completed_at else None,
        })

    order = {"critical": 0, "warning": 1, "healthy": 2, None: 3}
    resource_data.sort(key=lambda x: order.get(x["severity"], 3))

    critical = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "critical")
    warning = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "warning")
    healthy = sum(1 for r in resources if str(getattr(latest_evals.get(r.id), "severity", "")) == "healthy")

    return templates.TemplateResponse(request, "dashboard_overview.html", {
        "resources": resource_data,
        "total_resources": len(resources),
        "critical_count": critical,
        "warning_count": warning,
        "healthy_count": healthy,
    })


@router.get("/dashboard/resources", response_class=HTMLResponse)
async def dashboard_resources(request: Request):
    store = request.app.state.store
    resources = store.list_resources()
    latest_runs = store.get_latest_runs_batch()
    latest_evals = store.get_latest_evaluations_batch()

    resource_data = []
    for r in resources:
        ev = latest_evals.get(r.id)
        run = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        resource_data.append({
            "id": r.id,
            "name": r.name,
            "resource_type": r.resource_type,
            "severity": str(ev.severity) if ev else None,
            "summary": ev.summary if ev else None,
            "last_run_at": run.completed_at.strftime("%Y-%m-%d %H:%M") if run and run.completed_at else None,
        })

    # Sort: critical first, then warning, then healthy, then unknown
    order = {"critical": 0, "warning": 1, "healthy": 2, None: 3}
    resource_data.sort(key=lambda x: order.get(x["severity"], 3))

    return templates.TemplateResponse(request, "resource_list.html", {
        "resources": resource_data,
    })


@router.get("/resources", response_class=HTMLResponse)
async def resources_page(request: Request):
    """Redirect /resources to dashboard."""
    return await dashboard(request)


@router.get("/resources/new", response_class=HTMLResponse)
async def resource_new_form(request: Request, type: str = ""):
    return templates.TemplateResponse(request, "resource_new.html", {
        "resource": None,
        "editing": False,
        "selected_type": type if type in RESOURCE_TYPES else "",
        "resource_types": RESOURCE_TYPES,
    })


@router.get("/resources/{resource_id}/edit", response_class=HTMLResponse)
async def resource_edit_form(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return templates.TemplateResponse(request, "resource_new.html", {
        "resource": resource,
        "editing": True,
        "selected_type": resource.resource_type,
        "resource_types": RESOURCE_TYPES,
    })


@router.post("/resources/{resource_id}/edit")
async def resource_edit_submit(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    name = form.get("name", "").strip()
    if name:
        resource.name = name

    ssh_host = form.get("ssh_host", "").strip()
    if ssh_host:
        resource.config["ssh_host"] = ssh_host
        resource.config["ssh_user"] = form.get("ssh_user", "").strip() or "ubuntu"
        resource.config["ssh_key_path"] = form.get("ssh_key_path", "").strip()
        resource.config["ssh_port"] = form.get("ssh_port", "").strip() or "22"
    else:
        for k in ("ssh_host", "ssh_user", "ssh_key_path", "ssh_port"):
            resource.config.pop(k, None)

    store.save_resource(resource)

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/resources/{resource.id}", status_code=303)


@router.post("/resources/new")
async def resource_new_submit(request: Request):
    from ..models import Resource, Schedule

    store = request.app.state.store
    form = await request.form()
    name = form.get("name", "").strip()
    resource_type = form.get("resource_type", "server")

    if not name:
        return templates.TemplateResponse(request, "resource_new.html", {
            "resource": None, "editing": False,
            "selected_type": resource_type, "resource_types": RESOURCE_TYPES,
            "error": "Name is required.",
        }, status_code=400)
    if resource_type not in RESOURCE_TYPES:
        return templates.TemplateResponse(request, "resource_new.html", {
            "resource": None, "editing": False,
            "selected_type": "", "resource_types": RESOURCE_TYPES,
            "error": "Please select a resource type.",
        }, status_code=400)

    config: dict[str, str] = {}
    rtype = RESOURCE_TYPES[resource_type]

    # SSH fields (only for SSH-based types)
    if rtype["connection"] == "ssh":
        ssh_host = form.get("ssh_host", "").strip()
        if ssh_host:
            config["ssh_host"] = ssh_host
            config["ssh_user"] = form.get("ssh_user", "").strip() or "ubuntu"
            config["ssh_key_path"] = form.get("ssh_key_path", "").strip()
            config["ssh_port"] = form.get("ssh_port", "").strip() or "22"

    # Slack webhook
    slack = form.get("slack_webhook", "").strip()
    if slack:
        config["slack_webhook"] = slack

    # Schedules
    health_cron = form.get("health_cron", "").strip()
    discovery_cron = form.get("discovery_cron", "").strip()

    resource = Resource(
        name=name,
        resource_type=resource_type,
        config=config,
        health_check_schedule=Schedule(cron=health_cron, enabled=True) if health_cron else None,
        discovery_schedule=Schedule(cron=discovery_cron, enabled=True) if discovery_cron else None,
    )
    store.save_resource(resource)

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/resources/{resource.id}", status_code=303)


@router.get("/resources/{resource_id}", response_class=HTMLResponse)
async def resource_detail(resource_id: str, request: Request, page: int = 1):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    page = max(1, page)
    per_page = 10
    context = store.get_latest_context(resource_id)
    checklist = store.get_latest_checklist(resource_id)
    # Fetch one extra to detect if there are more
    recent_runs = store.get_runs(resource_id, limit=per_page + 1, offset=(page - 1) * per_page)
    has_more = len(recent_runs) > per_page
    recent_runs = recent_runs[:per_page]

    # Attach severity to runs
    runs_data = []
    for run in recent_runs:
        severity = None
        if run.evaluation_id:
            ev = store.get_evaluation(run.evaluation_id)
            if ev:
                severity = str(ev.severity)
        duration = None
        if run.started_at and run.completed_at:
            delta = run.completed_at - run.started_at
            duration = f"{int(delta.total_seconds())}s"
        runs_data.append({
            "run_type": str(run.run_type),
            "status": str(run.status),
            "severity": severity,
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "-",
            "duration": duration,
            "report_id": run.report_id,
        })

    latest_eval = store.get_recent_evaluations(resource_id, limit=1)
    severity = str(latest_eval[0].severity) if latest_eval else None

    context_html = _md_to_html(context.content) if context else ""

    # Current schedule values for form
    health_cron = resource.health_check_schedule.cron if resource.health_check_schedule else ""
    discovery_cron = resource.discovery_schedule.cron if resource.discovery_schedule else ""
    slack_webhook = resource.config.get("slack_webhook", "")

    return templates.TemplateResponse(request, "resource_detail.html", {
        "resource": resource,
        "context": context,
        "context_html": context_html,
        "checklist": checklist,
        "runs": runs_data,
        "severity": severity,
        "health_cron": health_cron,
        "discovery_cron": discovery_cron,
        "slack_webhook": slack_webhook,
        "page": page,
        "has_more_runs": has_more,
        "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


@router.post("/resources/{resource_id}/discover")
async def trigger_discover(resource_id: str, request: Request):
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    async def _run():
        try:
            await engine.run_discovery_async(resource_id)
        except Exception as e:
            logger.error("Dashboard discovery failed for %s: %s", resource_id, e)

    asyncio.create_task(_run())
    return Response(status_code=204)


@router.post("/resources/{resource_id}/health-check")
async def trigger_health_check(resource_id: str, request: Request):
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")

    async def _run():
        try:
            await engine.run_health_check_async(resource_id)
        except Exception as e:
            logger.error("Dashboard health check failed for %s: %s", resource_id, e)

    asyncio.create_task(_run())
    return Response(status_code=204)


@router.post("/resources/{resource_id}/toggle")
async def toggle_resource(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    resource.enabled = not resource.enabled
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/schedule")
async def update_schedule(resource_id: str, request: Request):
    from ..models import Schedule

    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    health_cron = form.get("health_cron", "").strip()
    discovery_cron = form.get("discovery_cron", "").strip()

    resource.health_check_schedule = Schedule(cron=health_cron, enabled=True) if health_cron else None
    resource.discovery_schedule = Schedule(cron=discovery_cron, enabled=True) if discovery_cron else None
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/notifications")
async def update_notifications(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    webhook = form.get("slack_webhook", "").strip()
    if webhook:
        resource.config["slack_webhook"] = webhook
    else:
        resource.config.pop("slack_webhook", None)
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/notify-test")
async def test_notification(resource_id: str, request: Request):
    from ..models import Evaluation, Report, Severity
    from ..notifications import send_alert

    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    test_report = Report(
        resource_id=resource.id,
        run_type="health_check",
        content="Test notification from Supervisor dashboard.",
    )
    test_eval = Evaluation(
        report_id=test_report.id,
        resource_id=resource.id,
        severity=Severity.WARNING,
        summary="Test notification — verifying webhook configuration",
        should_alert=True,
    )
    channels, _ = await send_alert(resource, test_report, test_eval, skip_dedup=True)
    if channels:
        return Response(status_code=204)
    raise HTTPException(status_code=400, detail="No notification channels configured or all failed")


@router.post("/resources/{resource_id}/checklist")
async def add_checklist_item(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    item_text = form.get("request", "").strip()
    if not item_text:
        raise HTTPException(status_code=400, detail="Check description required")

    if not resource.monitoring_requests:
        resource.monitoring_requests = []
    resource.monitoring_requests.append(item_text)
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/checklist-remove")
async def remove_checklist_item(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    try:
        index = int(form.get("index", "-1"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid index")

    if 0 <= index < len(resource.monitoring_requests):
        resource.monitoring_requests.pop(index)
        store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/delete")
async def delete_resource(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    store.delete_resource(resource_id)
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/", status_code=303)


@router.get("/dashboard/resources-status/{resource_id}")
async def resource_run_status(resource_id: str, request: Request):
    """JSON endpoint for run status polling from JS."""
    store = request.app.state.store
    from ..models import RunStatus

    runs = store.get_runs(resource_id, limit=1)
    if not runs:
        return {"running": False, "severity": None}

    latest = runs[0]
    is_running = latest.status in (RunStatus.PENDING, RunStatus.RUNNING)

    severity = None
    if not is_running and latest.evaluation_id:
        ev = store.get_evaluation(latest.evaluation_id)
        if ev:
            severity = str(ev.severity)

    return {"running": is_running, "severity": severity, "status": str(latest.status)}


def _md_to_html(text: str) -> str:
    """Minimal markdown to HTML. Handles headers, bold, code blocks, tables, lists."""
    lines = html_mod.escape(text).split("\n")
    out = []
    in_code = False
    in_table = False

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre class=\"report-view\"><code>")
                in_code = True
            continue
        if in_code:
            out.append(line)
            continue

        # Close table if line doesn't start with |
        if in_table and not line.strip().startswith("|"):
            out.append("</tbody></table></div>")
            in_table = False

        stripped = line.strip()

        # Skip table separator rows
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                out.append('<div class="table-wrap"><table class="table"><thead><tr>')
                out.append("".join(f"<th>{c}</th>" for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue

        # Headers
        if stripped.startswith("### "):
            out.append(f"<h4>{stripped[4:]}</h4>")
            continue
        if stripped.startswith("## "):
            out.append(f"<h3>{stripped[3:]}</h3>")
            continue
        if stripped.startswith("# "):
            out.append(f"<h2>{stripped[2:]}</h2>")
            continue

        # Horizontal rule
        if stripped == "---":
            out.append("<hr>")
            continue

        # List items
        if stripped.startswith("- "):
            content = stripped[2:]
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
            out.append(f"<li>{content}</li>")
            continue

        # Bold/inline code
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)

        if stripped:
            out.append(f"<p>{line}</p>")
        else:
            out.append("")

    if in_code:
        out.append("</code></pre>")
    if in_table:
        out.append("</tbody></table></div>")

    return "\n".join(out)


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(report_id: str, request: Request):
    store = request.app.state.store
    report = store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    evaluation = None
    evals = store.get_recent_evaluations(report.resource_id, limit=20)
    for ev in evals:
        if ev.report_id == report_id:
            evaluation = ev
            break

    resource = store.get_resource(report.resource_id)
    resource_name = resource.name if resource else report.resource_id

    report_html = _md_to_html(report.content or "")

    return templates.TemplateResponse(request, "report_detail.html", {
        "report": report,
        "report_html": report_html,
        "evaluation": evaluation,
        "resource_name": resource_name,
    })
