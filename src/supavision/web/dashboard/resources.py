"""Resource CRUD, wizard, detail, streaming, and reports."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from ...models import Credential, RunStatus, RunType
from ...resource_types import RESOURCE_TYPES, WIZARD_FLOWS
from ...secrets_policy import (
    is_valid_env_var_name,
    secret_keys_present,
    validate_credentials_env_vars,
    validate_no_raw_secrets,
)
from ..run_triggers import trigger_run_or_409
from . import _check_rate_limit, _md_to_html, _render, _require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# Type-aware impact strings for resource cards
_IMPACT_MAP = {
    ("critical", "server"): "Server may be down or degraded",
    ("critical", "database"): "Database queries or connections may be failing",
    ("critical", "aws_account"): "AWS services may be impacted",
    ("critical", "github_org"): "Repository workflows may be failing",
    ("warning", "server"): "Server performance may be affected",
    ("warning", "database"): "Database performance may degrade",
    ("warning", "aws_account"): "AWS costs or security may need attention",
    ("warning", "github_org"): "Code quality or security may need review",
}


def _freshness(last_run_at_iso: str | None) -> str:
    """Bucket a last-run timestamp into fresh / aging / stale / never.

    Fixed absolute thresholds (not schedule-relative): users want to know
    "how recent is this data, period?" — schedule-relative would mark a
    weekly-cadence resource fresh for 6 days even when stale.
    """
    if not last_run_at_iso:
        return "never"
    try:
        ts = datetime.fromisoformat(last_run_at_iso)
    except (TypeError, ValueError):
        return "never"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age < 3600:
        return "fresh"
    if age < 86400:
        return "aging"
    return "stale"


@router.get("/resources", response_class=HTMLResponse)
async def resources_page(request: Request):
    """Dedicated resource list page."""
    from ...models import RunType

    store = request.app.state.store
    resources = store.list_resources()
    latest_evals = store.get_latest_evaluations_batch()
    latest_runs = store.get_latest_runs_batch()

    resource_data = []
    for r in resources:
        ev = latest_evals.get(r.id)
        run = latest_runs.get((r.id, str(RunType.HEALTH_CHECK)))
        severity = str(ev.severity) if ev else None
        summary = ev.summary[:120] if ev and ev.summary else None
        last_run_at = None
        if run and (run.completed_at or run.started_at):
            last_run_at = (run.completed_at or run.started_at).isoformat()

        # Derive actionable card content from state
        if not r.enabled:
            explanation = "Monitoring paused"
            impact = "Not being monitored"
            action_label = "Enable"
            action_url = f"/resources/{r.id}/edit"
            action_is_link = True
        elif severity == "critical":
            explanation = summary or "Issue detected \u2014 run a check for details"
            impact = _IMPACT_MAP.get(("critical", r.resource_type), "Critical issue detected")
            action_label = "Investigate"
            action_url = f"/resources/{r.id}"
            action_is_link = True
        elif severity == "warning":
            explanation = summary or "Issue detected \u2014 run a check for details"
            impact = _IMPACT_MAP.get(("warning", r.resource_type), "May escalate if unaddressed")
            action_label = "Review"
            action_url = f"/resources/{r.id}"
            action_is_link = True
        elif severity == "healthy":
            explanation = "All checks passing"
            impact = "Last check found no issues"
            action_label = "View Details"
            action_url = f"/resources/{r.id}"
            action_is_link = True
        else:  # no data yet
            explanation = "Awaiting first check"
            impact = "Health state unknown until checked"
            action_label = "Run Check"
            action_url = f"/resources/{r.id}/health-check"
            action_is_link = False  # HTMX trigger

        resource_data.append(
            {
                "id": r.id,
                "name": r.name,
                "resource_type": r.resource_type,
                "severity": severity,
                "summary": summary,
                "last_run_at": last_run_at,
                "freshness": _freshness(last_run_at),
                "enabled": r.enabled,
                "explanation": explanation,
                "impact": impact,
                "action_label": action_label,
                "action_url": action_url,
                "action_is_link": action_is_link,
            }
        )

    return _render(
        request,
        "resources_list.html",
        {
            "resources": resource_data,
            "total": len(resources),
        },
    )


# ── Wizard ────────────────────────────────────────────────────


@router.get("/resources/new", response_class=HTMLResponse)
async def resource_new_form(request: Request, type: str = ""):
    """Type selector or wizard step 1."""
    selected = type if type in WIZARD_FLOWS else ""
    rtype = RESOURCE_TYPES.get(selected, {})
    flow = WIZARD_FLOWS.get(selected, [])

    if not selected:
        # Type selector page
        return _render(
            request,
            "resource_new.html",
            {
                "resource": None,
                "editing": False,
                "selected_type": "",
                "resource_types": RESOURCE_TYPES,
            },
        )

    # Wizard step 1
    return _render(
        request,
        "resource_new.html",
        {
            "resource": None,
            "editing": False,
            "selected_type": selected,
            "resource_types": RESOURCE_TYPES,
            "wizard_flow": flow,
            "current_wizard_step": 1,
            "total_steps": len(flow),
            "next_step_label": flow[1][0] if len(flow) > 1 else None,
            "resource_type": selected,
            "type_label": rtype.get("label", selected),
            "how_it_works": rtype.get("how_it_works", ""),
            "data": {},
        },
    )


# ── Wizard helpers ────────────────────────────────────────────


def _collect_wizard_data(form) -> dict:
    """Collect all wizard form data into a dict, excluding internals."""
    data = {}
    skip = {"csrf_token"}
    for key in form:
        if key in skip:
            continue
        val = form[key]
        if isinstance(val, str) and val.strip():
            data[key] = val.strip()
    return data


def _env_var_status(data: dict) -> dict[str, bool]:
    """For each '<key>_env_var' value in `data`, report whether it's set in os.environ.

    Used by the credentials wizard step to render a ✓/✗ indicator next to each
    env-var-name field the user has filled in.
    """
    status: dict[str, bool] = {}
    for key, val in data.items():
        if not key.endswith("_env_var"):
            continue
        if not isinstance(val, str) or not val.strip():
            continue
        name = val.strip()
        status[name] = bool(os.environ.get(name))
    return status


def _schedule_preview(cron_expr: str, count: int = 5) -> list[str]:
    """Return the next `count` run times for a cron expression as ISO strings.

    Returns an empty list for an empty or invalid expression — the template
    just doesn't render the preview block in that case.
    """
    if not cron_expr:
        return []
    try:
        from croniter import croniter

        it = croniter(cron_expr, datetime.now(timezone.utc))
        return [it.get_next(datetime).isoformat() for _ in range(count)]
    except (ValueError, KeyError):
        return []


def _wizard_context(resource_type: str, step: int, data: dict, **extra) -> dict:
    """Build template context for a wizard step."""
    rtype = RESOURCE_TYPES.get(resource_type, {})
    flow = WIZARD_FLOWS.get(resource_type, [])

    # Compute next step label for the "Next: ..." button
    next_label = None
    if step < len(flow):
        next_label = flow[step][0]  # flow[step] is the NEXT step (0-indexed vs 1-indexed)

    return {
        "resource_type": resource_type,
        "type_label": rtype.get("label", resource_type),
        "how_it_works": rtype.get("how_it_works", ""),
        "wizard_flow": flow,
        "current_wizard_step": step,
        "total_steps": len(flow),
        "next_step_label": next_label,
        "data": data,
        "env_var_status": _env_var_status(data),
        "health_schedule_preview": _schedule_preview(data.get("health_cron", "")),
        "discovery_schedule_preview": _schedule_preview(data.get("discovery_cron", "")),
        **extra,
    }


def _validate_step(resource_type: str, step: int, data: dict) -> list[str]:
    """Validate fields for the current step. Returns list of error strings."""
    flow = WIZARD_FLOWS.get(resource_type, [])
    if step < 1 or step > len(flow):
        return ["Invalid step"]
    _, suffix = flow[step - 1]
    errors = []

    if suffix == "resource_info":
        if not data.get("name", "").strip():
            errors.append("Display name is required.")
        elif len(data["name"]) > 100:
            errors.append("Display name must be 100 characters or less.")
        if resource_type == "server":
            if not data.get("host"):
                errors.append("SSH host or IP is required.")
            if not data.get("target_directory"):
                errors.append("Target directory is required.")
        elif resource_type == "github_org":
            if not data.get("github_org"):
                errors.append("Organization name is required.")
        elif resource_type == "database":
            if not data.get("db_engine"):
                errors.append("Database engine is required.")

    elif suffix == "credentials":
        if resource_type == "aws_account":
            required = (
                ("aws_access_key_env_var", "Access Key ID env var name"),
                ("aws_secret_key_env_var", "Secret Access Key env var name"),
            )
        elif resource_type == "github_org":
            required = (("github_token_env_var", "Personal Access Token env var name"),)
        else:
            required = ()
        for field, label in required:
            val = data.get(field, "").strip()
            if not val:
                errors.append(f"{label} is required.")
            elif not is_valid_env_var_name(val):
                errors.append(
                    f"{label} must be a valid environment variable name "
                    "(uppercase letters, digits, underscores; cannot start with a digit)."
                )

    elif suffix == "db_connection":
        method = data.get("db_connection_method", "direct")
        if method == "ssh":
            if not data.get("ssh_host"):
                errors.append("SSH host is required for tunnel mode.")
        if not data.get("db_host"):
            errors.append("Database host is required.")
        if not data.get("db_name"):
            errors.append("Database name is required.")

    # ssh_key, test_connection, schedule, confirm: no blocking validation
    return errors


# ── Generic wizard routes ─────────────────────────────────────


@router.post("/resources/wizard/next")
async def wizard_next(request: Request):
    """Validate current step and render the next step."""
    _require_admin(request)
    form = await request.form()
    data = _collect_wizard_data(form)
    resource_type = data.get("resource_type", "")
    current_step = int(data.get("_step", "1"))

    flow = WIZARD_FLOWS.get(resource_type)
    if not flow:
        raise HTTPException(400, "Invalid resource type")
    if current_step < 1 or current_step > len(flow):
        raise HTTPException(400, "Invalid step")

    # Validate current step
    errors = _validate_step(resource_type, current_step, data)
    if errors:
        _, suffix = flow[current_step - 1]
        ctx = _wizard_context(resource_type, current_step, data, errors=errors)
        return _render(request, f"_wizard_{suffix}.html", ctx)

    # Advance to next step
    next_step = current_step + 1
    if next_step > len(flow):
        raise HTTPException(400, "Already at last step")

    _, next_suffix = flow[next_step - 1]
    ctx = _wizard_context(resource_type, next_step, data)

    # Step-specific context enrichment
    if next_suffix == "ssh_key":
        from ...ssh_keys import ensure_ssh_keypair

        key_path = data.get("ssh_key_path") or None
        try:
            resolved_path, public_key = ensure_ssh_keypair(key_path)
            data["ssh_key_path"] = resolved_path
            ctx["public_key"] = public_key
            ctx["key_generated"] = True
            ctx["data"] = data  # Update with resolved path
        except Exception as e:
            ctx["key_error"] = str(e)
            ctx["key_generated"] = False

    elif next_suffix == "test_connection":
        ctx["test_result"] = None  # Template auto-fires test via HTMX

    return _render(request, f"_wizard_{next_suffix}.html", ctx)


@router.post("/resources/wizard/back")
async def wizard_back(request: Request):
    """Go back one step (no validation)."""
    _require_admin(request)
    form = await request.form()
    data = _collect_wizard_data(form)
    resource_type = data.get("resource_type", "")
    current_step = int(data.get("_step", "2"))

    flow = WIZARD_FLOWS.get(resource_type)
    if not flow:
        raise HTTPException(400, "Invalid resource type")

    prev_step = max(1, current_step - 1)
    _, prev_suffix = flow[prev_step - 1]
    ctx = _wizard_context(resource_type, prev_step, data)

    # Re-enrich ssh_key step context if going back to it
    if prev_suffix == "ssh_key":
        from ...ssh_keys import ensure_ssh_keypair

        try:
            resolved_path, public_key = ensure_ssh_keypair(data.get("ssh_key_path"))
            ctx["public_key"] = public_key
            ctx["key_generated"] = True
        except Exception as e:
            ctx["key_error"] = str(e)
            ctx["key_generated"] = False

    return _render(request, f"_wizard_{prev_suffix}.html", ctx)


@router.post("/resources/wizard/test")
async def wizard_test_connection(request: Request):
    """Test SSH or database connection. Returns JSON for HTMX."""
    _require_admin(request)
    import subprocess as _subprocess

    form = await request.form()
    data = _collect_wizard_data(form)

    host = data.get("host", data.get("ssh_host", "")).strip()
    user = data.get("user", data.get("ssh_user", "ubuntu")).strip()
    port = data.get("port", data.get("ssh_port", "22")).strip()
    key_path = data.get("ssh_key_path", "").strip()

    if not host:
        return {"ok": False, "message": "No host configured."}

    # Build SSH command
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        port,
    ]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.extend([f"{user}@{host}", "echo ok"])

    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return {
                "ok": True,
                "message": f"Connected to {user}@{host}:{port}",
            }
        err = r.stderr.strip()[:200]
        return {"ok": False, "message": f"Connection failed: {err}"}
    except _subprocess.TimeoutExpired:
        return {"ok": False, "message": "Connection timed out after 10s."}
    except Exception as e:
        return {"ok": False, "message": f"Error: {str(e)[:200]}"}


@router.get("/resources/{resource_id}/edit", response_class=HTMLResponse)
async def resource_edit_form(resource_id: str, request: Request):
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    health_cron = resource.health_check_schedule.cron if resource.health_check_schedule else ""
    discovery_cron = resource.discovery_schedule.cron if resource.discovery_schedule else ""
    slack_cred = resource.credentials.get("slack_webhook")
    slack_webhook_env_var = slack_cred.env_var if slack_cred else ""
    legacy_secret_keys = secret_keys_present(resource.config)

    next_health_check = None
    if health_cron and resource.enabled:
        try:
            from croniter import croniter

            cron = croniter(health_cron, datetime.now(timezone.utc))
            next_health_check = cron.get_next(datetime).isoformat()
        except Exception:
            pass

    return _render(
        request,
        "resource_edit.html",
        {
            "resource": resource,
            "resource_types": RESOURCE_TYPES,
            "health_cron": health_cron,
            "discovery_cron": discovery_cron,
            "slack_webhook_env_var": slack_webhook_env_var,
            "legacy_secret_keys": legacy_secret_keys,
            "next_health_check": next_health_check,
        },
    )


@router.post("/resources/{resource_id}/edit")
async def resource_edit_submit(resource_id: str, request: Request):
    _require_admin(request)
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()

    # Defense in depth: reject any raw secret-shaped field in the submitted form.
    # The edit form is supposed to only carry SSH/host fields, not credentials.
    # A direct POST (curl, scripted client, tampered DOM) that tries to slip a
    # raw secret in must be turned away — and we must NOT silently let the edit
    # introduce *new* raw secrets just because the row already had legacy ones.
    submitted_config_like = {k: v for k, v in form.items() if isinstance(v, str)}
    raw_offenders = validate_no_raw_secrets(submitted_config_like)
    if raw_offenders:
        raise HTTPException(
            400,
            "Supavision does not store raw secrets. Use the Notifications section "
            "to reference an env var instead. Offending fields: "
            f"{', '.join(raw_offenders)}.",
        )

    name = form.get("name", "").strip()
    if name:
        if len(name) > 200:
            raise HTTPException(400, "Resource name must be 200 characters or fewer.")
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

    for ck, cv in resource.config.items():
        if len(cv) > 500:
            raise HTTPException(400, f"Config value for '{ck}' must be 500 characters or fewer.")

    store.save_resource(resource)

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/resources/{resource.id}", status_code=303)


@router.post("/resources/test-connection")
async def test_connection(request: Request):
    """Test SSH connection before creating resource."""
    _require_admin(request)
    import subprocess

    form = await request.form()
    result = {"ok": False, "message": ""}

    host = form.get("ssh_host", "").strip()
    user = form.get("ssh_user", "ubuntu").strip()
    port = form.get("ssh_port", "22").strip()
    key_path = form.get("ssh_key_path", "").strip()

    if not host:
        result["message"] = "SSH host is required"
    else:
        cmd = [
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            port,
        ]
        if key_path:
            cmd.extend(["-i", key_path])
        cmd.extend([f"{user}@{host}", "echo ok"])

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                result = {"ok": True, "message": f"Connected to {user}@{host}:{port}"}
            else:
                err = r.stderr.strip()[:100]
                result["message"] = f"Connection failed: {err}"
        except subprocess.TimeoutExpired:
            result["message"] = "Connection timed out after 10s"
        except Exception as e:
            result["message"] = f"Error: {str(e)[:100]}"

    return result


@router.post("/resources/new")
async def resource_new_submit(request: Request):
    """Final wizard submission — create the resource."""
    _require_admin(request)
    from ...models import Resource, Schedule

    store = request.app.state.store
    form = await request.form()
    name = form.get("name", "").strip()
    resource_type = form.get("resource_type", "server")

    if not name:
        raise HTTPException(400, "Name is required.")
    if len(name) > 200:
        raise HTTPException(400, "Resource name must be 200 characters or fewer.")
    if resource_type not in RESOURCE_TYPES:
        raise HTTPException(400, "Invalid resource type.")

    config: dict[str, str] = {}

    # SSH fields (server, database w/ SSH tunnel)
    host = form.get("host", form.get("ssh_host", "")).strip()
    if host:
        config["ssh_host"] = host
        config["ssh_user"] = form.get("user", form.get("ssh_user", "")).strip() or "ubuntu"
        config["ssh_key_path"] = form.get("ssh_key_path", "").strip()
        config["ssh_port"] = form.get("port", form.get("ssh_port", "")).strip() or "22"

    # Target directory (server)
    target_dir = form.get("target_directory", "").strip()
    if target_dir:
        config["target_directory"] = target_dir

    # Database fields
    if resource_type == "database":
        for field in ("db_engine", "db_host", "db_port", "db_name", "db_user", "db_connection_method"):
            val = form.get(field, "").strip()
            if val:
                config[field] = val

    # GitHub org
    github_org = form.get("github_org", "").strip()
    if github_org:
        config["github_org"] = github_org

    # Defense in depth: reject any raw secret-shaped field in the submitted form
    # before doing anything else. A stale browser, tampered form, or future field
    # rename could try to slip raw values in alongside the canonical *_env_var
    # fields. Scan the form (not just the post-processed config) so an ignored
    # field can't bypass the check.
    submitted = {k: v for k, v in form.items() if isinstance(v, str)}
    form_offenders = validate_no_raw_secrets(submitted)
    if form_offenders:
        raise HTTPException(
            400,
            "Supavision does not store raw secrets. Pass these as env-var references "
            f"on the Credentials step (field name '<name>_env_var'): "
            f"{', '.join(form_offenders)}.",
        )

    # Credentials — env-var references only. Field name '<credential>_env_var' carries
    # the NAME of an env var that holds the actual secret; the secret value itself is
    # never accepted by Supavision.
    credentials_env_vars: dict[str, str] = {}
    for cred_name, field in (
        ("aws_access_key", "aws_access_key_env_var"),
        ("aws_secret_key", "aws_secret_key_env_var"),
        ("github_token", "github_token_env_var"),
        ("slack_webhook", "slack_webhook_env_var"),
    ):
        val = form.get(field, "").strip()
        if val:
            credentials_env_vars[cred_name] = val

    bad_env = validate_credentials_env_vars(credentials_env_vars)
    if bad_env:
        raise HTTPException(
            400,
            f"Credential env var names must be uppercase letters/digits/underscores (invalid: {', '.join(bad_env)}).",
        )

    # Defense in depth: reject any raw-secret-shaped key the wizard might have
    # collected into config (e.g. notes containing a key=value pair).
    raw_offenders = validate_no_raw_secrets(config)
    if raw_offenders:
        raise HTTPException(
            400,
            "Supavision does not store raw secrets. The following fields must be passed "
            f"as env-var references on the Credentials step instead of as raw values: "
            f"{', '.join(raw_offenders)}.",
        )

    # Notes
    notes = form.get("notes", "").strip()
    if notes:
        config["notes"] = notes

    # Config size guard
    if len(config) > 50:
        raise HTTPException(400, "Config cannot have more than 50 entries.")
    for ck, cv in config.items():
        if len(cv) > 500:
            raise HTTPException(400, f"Config value for '{ck}' must be 500 characters or fewer.")

    # Monitoring requests (textarea → list); silently cap to avoid bad UX on free-text field
    monitoring_requests_raw = form.get("monitoring_requests", "").strip()
    monitoring_requests = (
        [line.strip()[:500] for line in monitoring_requests_raw.split("\n") if line.strip()][:50]
        if monitoring_requests_raw
        else []
    )

    # Schedules
    health_cron = form.get("health_cron", "").strip()
    discovery_cron = form.get("discovery_cron", "").strip()

    credentials = {name: Credential(env_var=ev) for name, ev in credentials_env_vars.items()}
    resource = Resource(
        name=name,
        resource_type=resource_type,
        config=config,
        credentials=credentials,
        monitoring_requests=monitoring_requests,
        health_check_schedule=Schedule(cron=health_cron, enabled=True) if health_cron else None,
        discovery_schedule=Schedule(cron=discovery_cron, enabled=True) if discovery_cron else None,
    )
    store.save_resource(resource)

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/resources/{resource.id}?new=1", status_code=303)


@router.get("/resources/{resource_id}", response_class=HTMLResponse)
async def resource_detail(resource_id: str, request: Request, page: int = 1, new: int = 0):
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
        # Workstream B: mini diff summary per run (from structured payload)
        diff_new = diff_resolved = 0
        if run.report_id:
            report = store.get_report(run.report_id)
            if report and report.payload_diff:
                diff_new = len(report.payload_diff.new)
                diff_resolved = len(report.payload_diff.resolved)
        runs_data.append(
            {
                "run_type": str(run.run_type),
                "status": str(run.status),
                "severity": severity,
                "started_at": run.started_at.isoformat() if run.started_at else "-",
                "duration": duration,
                "report_id": run.report_id,
                "error": (run.error[:150] + "...") if run.error and len(run.error) > 150 else run.error,
                "diff_new": diff_new,
                "diff_resolved": diff_resolved,
            }
        )

    latest_eval = store.get_recent_evaluations(resource_id, limit=1)
    severity = str(latest_eval[0].severity) if latest_eval else None

    context_html = _md_to_html(context.content) if context else ""

    # Current schedule values for form
    health_cron = resource.health_check_schedule.cron if resource.health_check_schedule else ""
    discovery_cron = resource.discovery_schedule.cron if resource.discovery_schedule else ""
    slack_webhook = resource.config.get("slack_webhook", "")

    # Next scheduled run
    next_health_check = None
    if health_cron and resource.enabled:
        try:
            from croniter import croniter

            c = croniter(health_cron, datetime.now(timezone.utc))
            next_health_check = c.get_next(datetime).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    # Alert history from evaluations where should_alert=True
    all_evals = store.get_recent_evaluations(resource_id, limit=30)
    alert_evals = [
        {
            "severity": str(e.severity),
            "summary": e.summary[:120] + ("..." if len(e.summary) > 120 else ""),
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in all_evals
        if e.should_alert
    ][:10]

    # 30-day health grid
    from datetime import timedelta

    health_grid_raw = store.get_health_grid(resource_id, days=30)
    today = datetime.now(timezone.utc).date()
    health_grid = []
    for i in range(30):
        day = today - timedelta(days=29 - i)
        day_str = day.isoformat()
        severities = health_grid_raw.get(day_str, [])
        # Use worst severity of the day
        if "critical" in severities:
            sev = "critical"
        elif "warning" in severities:
            sev = "warning"
        elif "healthy" in severities:
            sev = "healthy"
        else:
            sev = None
        health_grid.append({"date": day_str, "severity": sev})

    # Detect active and last run for live output terminal
    active_run = None
    sse_url = None
    last_run = recent_runs[0] if recent_runs else None
    if last_run and str(last_run.status) in ("pending", "running"):
        active_run = last_run
        sse_url = f"/resources/{resource_id}/runs/{last_run.id}/stream"

    # Active issues for the new top-of-page panel.
    # Source order:
    #   1. The latest health-check report's structured payload.issues
    #      (canonical — has severity, evidence, recommendation, stable IDs).
    #   2. Fallback for legacy reports: Evaluation.summary as a single
    #      severity-coloured note. (Evaluation only carries resource-level
    #      severity + summary; it is NOT a substitute for an issue list.)
    # Use dedicated single-row lookups for workflow + latest-report derivation.
    # The paginated `recent_runs` slice is for the history table only — for
    # a resource with >per_page health-check runs since its last discovery,
    # `recent_runs` won't contain the discovery row, and a naive
    # `any(... for r in recent_runs)` check would wrongly say "needs discovery".
    latest_discovery_run = store.get_latest_run(resource.id, RunType.DISCOVERY)
    latest_health_run = store.get_latest_run(resource.id, RunType.HEALTH_CHECK)

    active_issues: list[dict] = []
    legacy_issue_summary: str | None = None
    if latest_health_run and latest_health_run.report_id:
        latest_report = store.get_report(latest_health_run.report_id)
        if latest_report and latest_report.payload and latest_report.payload.issues:
            severity_order = {"critical": 0, "warning": 1, "info": 2}
            for issue in sorted(
                latest_report.payload.issues,
                key=lambda i: severity_order.get(str(i.severity), 99),
            ):
                active_issues.append(
                    {
                        "id": issue.id,
                        "title": issue.title,
                        "severity": str(issue.severity),
                        "evidence": issue.evidence,
                        "recommendation": issue.recommendation,
                        "tags": list(issue.tags),
                        "scope": issue.scope,
                    }
                )
        elif latest_eval:
            # Legacy: no structured payload → show the evaluation summary.
            legacy_issue_summary = latest_eval[0].summary

    # Workflow step indicator: where is this resource in the flagship journey?
    has_discovery = latest_discovery_run is not None and str(latest_discovery_run.status) == "completed"
    has_health_check = latest_health_run is not None and str(latest_health_run.status) == "completed"
    if not has_discovery:
        workflow_step = "needs_discovery"
        recommended_action = {
            "label": "Run Discovery",
            "hint": "Map this resource and establish a baseline before the first health check.",
            "href": f"/resources/{resource_id}/discover",
            "method": "post",
        }
    elif not has_health_check:
        workflow_step = "needs_health_check"
        recommended_action = {
            "label": "Run Health Check",
            "hint": "Baseline is set — run your first health check to surface issues.",
            "href": f"/resources/{resource_id}/health-check",
            "method": "post",
        }
    elif active_issues and severity in ("critical", "warning"):
        workflow_step = "verify_fix"
        recommended_action = {
            "label": "Verify Fix",
            "hint": "Re-run the health check after addressing the issues above; the diff will show what changed.",
            "href": f"/resources/{resource_id}/health-check",
            "method": "post",
        }
    else:
        workflow_step = "healthy"
        recommended_action = {
            "label": "Run Health Check",
            "hint": "Everything looks healthy. Run an ad-hoc check anytime.",
            "href": f"/resources/{resource_id}/health-check",
            "method": "post",
        }

    return _render(
        request,
        "resource_detail.html",
        {
            "resource": resource,
            "context": context,
            "context_html": context_html,
            "checklist": checklist,
            "runs": runs_data,
            "alert_history": alert_evals,
            "severity": severity,
            "latest_eval": latest_eval[0] if latest_eval else None,
            "active_issues": active_issues,
            "legacy_issue_summary": legacy_issue_summary,
            "workflow_step": workflow_step,
            "recommended_action": recommended_action,
            "health_cron": health_cron,
            "discovery_cron": discovery_cron,
            "slack_webhook": slack_webhook,
            "next_health_check": next_health_check,
            "page": page,
            "has_more_runs": has_more,
            "is_new": bool(new),
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "engine_available": request.app.state.engine is not None,
            "active_run": active_run,
            "sse_url": sse_url,
            "last_run": last_run,
            "health_grid": health_grid,
        },
    )


@router.get("/resources/{resource_id}/history", response_class=HTMLResponse)
async def resource_history(resource_id: str, request: Request, page: int = 1):
    """Timeline of all runs, baselines, and changes for a resource."""
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    per_page = 30
    runs = store.get_runs(resource_id, limit=per_page + 1, offset=(page - 1) * per_page)
    has_more = len(runs) > per_page
    runs = runs[:per_page]

    # Get baseline versions for context
    contexts = store.get_context_history(resource_id, limit=10)
    context_versions = {c.created_at.isoformat()[:10]: c.version for c in contexts}

    events = []
    for run in runs:
        ev = store.get_evaluation(run.evaluation_id) if run.evaluation_id else None
        severity = str(ev.severity) if ev else None
        summary = ev.summary[:100] if ev else str(run.status)

        # Check if this run produced a new baseline
        is_baseline = str(run.run_type) == "discovery" and str(run.status) == "completed"
        version = None
        if is_baseline and run.completed_at:
            day = run.completed_at.isoformat()[:10]
            version = context_versions.get(day)

        events.append(
            {
                "type": str(run.run_type),
                "severity": severity,
                "summary": summary,
                "error": run.error[:120] if run.error else None,
                "report_id": run.report_id,
                "created_at": (run.started_at or run.created_at).isoformat(),
                "is_baseline": is_baseline,
                "version": version,
            }
        )

    return _render(
        request,
        "resource_history.html",
        {
            "resource": resource,
            "events": events,
            "page": page,
            "has_more": has_more,
        },
    )


@router.post("/resources/{resource_id}/discover")
async def trigger_discover(resource_id: str, request: Request):
    _require_admin(request)
    if not _check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in a minute.")
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    # 202 with {ok, run_id} on success; 409 with {error, active_run_id, hint}
    # if a PENDING/RUNNING run already exists.
    return trigger_run_or_409(store, engine, resource_id, RunType.DISCOVERY)


@router.post("/resources/{resource_id}/health-check")
async def trigger_health_check(resource_id: str, request: Request):
    _require_admin(request)
    if not _check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in a minute.")
    store = request.app.state.store
    engine = request.app.state.engine
    if not store.get_resource(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return trigger_run_or_409(store, engine, resource_id, RunType.HEALTH_CHECK)


@router.post("/resources/{resource_id}/toggle")
async def toggle_resource(resource_id: str, request: Request):
    _require_admin(request)
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    resource.enabled = not resource.enabled
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/schedule")
async def update_schedule(resource_id: str, request: Request):
    _require_admin(request)
    from ...models import Schedule

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
    _require_admin(request)
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()

    # The legacy field is `slack_webhook` (a raw URL); the new field is
    # `slack_webhook_env_var` (the name of an env var that holds the URL).
    # Reject the legacy field entirely — webhook URLs are credentials.
    if form.get("slack_webhook", "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "Supavision does not store webhook URLs. Set an environment "
                "variable holding the URL and pass its name via `slack_webhook_env_var`."
            ),
        )

    env_var = form.get("slack_webhook_env_var", "").strip()
    if env_var:
        if not is_valid_env_var_name(env_var):
            raise HTTPException(
                status_code=400,
                detail=(
                    "`slack_webhook_env_var` must be a valid environment variable name "
                    "(uppercase letters, digits, underscores; cannot start with a digit)."
                ),
            )
        resource.credentials["slack_webhook"] = Credential(env_var=env_var)
        # Editing the credential is the natural migration path for legacy raw values.
        resource.config.pop("slack_webhook", None)
    else:
        # Empty submit = remove the configuration entirely (both new and legacy).
        resource.credentials.pop("slack_webhook", None)
        resource.config.pop("slack_webhook", None)

    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/notify-test")
async def test_notification(resource_id: str, request: Request):
    _require_admin(request)
    from ...models import Evaluation, Report, Severity
    from ...notifications import send_alert

    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    test_report = Report(
        resource_id=resource.id,
        run_type="health_check",
        content="Test notification from Supavision dashboard.",
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
    _require_admin(request)
    store = request.app.state.store
    resource = store.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    form = await request.form()
    item_text = form.get("request", "").strip()
    if not item_text:
        raise HTTPException(status_code=400, detail="Check description required")
    if len(item_text) > 500:
        raise HTTPException(status_code=400, detail="Monitoring request must be 500 characters or fewer.")
    if len(resource.monitoring_requests) >= 50:
        raise HTTPException(status_code=400, detail="Cannot have more than 50 monitoring requests.")

    if not resource.monitoring_requests:
        resource.monitoring_requests = []
    resource.monitoring_requests.append(item_text)
    store.save_resource(resource)
    return Response(status_code=204)


@router.post("/resources/{resource_id}/checklist-remove")
async def remove_checklist_item(resource_id: str, request: Request):
    _require_admin(request)
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
    raise HTTPException(status_code=400, detail="Invalid checklist index")


@router.post("/resources/{resource_id}/delete")
async def delete_resource(resource_id: str, request: Request):
    _require_admin(request)
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

    error = None
    if latest.status == RunStatus.FAILED and latest.error:
        error = latest.error[:200]  # Truncate long tracebacks

    return {
        "running": is_running,
        "severity": severity,
        "status": str(latest.status),
        "error": error,
        "run_id": latest.id if is_running else None,
    }


@router.get("/resources/{resource_id}/runs/{run_id}/stream")
async def stream_run_output(resource_id: str, run_id: str, request: Request):
    """SSE endpoint — streams live Claude CLI output during a run.

    Events are JSON: {"t": delay_ms, "d": "line text"} for terminal replay.
    Status events: event:status {"state":"running"} and event:done {"state":"completed"}.
    """
    import json as _json

    from fastapi.responses import StreamingResponse

    from ...engine import get_run_buffer

    async def event_stream():
        cursor = 0
        sent_status = False
        while True:
            if await request.is_disconnected():
                return
            events, done = get_run_buffer(run_id)
            if not sent_status and (events or done):
                state = "completed" if done else "running"
                yield f"event: status\ndata: {_json.dumps({'state': state})}\n\n"
                sent_status = True
            if cursor < len(events):
                for ts, text in events[cursor:]:
                    msg = _json.dumps({"t": round(ts * 1000), "d": text})
                    yield f"data: {msg}\n\n"
                cursor = len(events)
            if done:
                yield f"event: done\ndata: {_json.dumps({'state': 'completed'})}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    return _render(
        request,
        "report_detail.html",
        {
            "report": report,
            "report_html": report_html,
            "evaluation": evaluation,
            "resource_name": resource_name,
            "base_url": os.environ.get("SUPAVISION_BASE_URL", "").rstrip("/"),
            "summary_text": _report_to_summary(report, evaluation, resource),
        },
    )


def _report_to_summary(report, evaluation, resource) -> str:
    """Short Slack-pasteable summary. Issue list capped at 5 entries."""
    name = resource.name if resource else report.resource_id
    severity = None
    if evaluation is not None and evaluation.severity:
        severity = str(evaluation.severity).upper()
    elif report.payload is not None:
        severity = str(report.payload.status).upper()

    header = f"**{name}**"
    if severity and severity.lower() != "unknown":
        header += f" — {severity}"

    parts: list[str] = [header]

    summary_text = None
    if evaluation is not None and evaluation.summary:
        summary_text = evaluation.summary
    elif report.payload is not None and report.payload.summary:
        summary_text = report.payload.summary
    if summary_text:
        parts.extend(["", summary_text])

    if report.payload is not None and report.payload.issues:
        parts.append("")
        for issue in report.payload.issues[:5]:
            parts.append(f"- [{issue.severity}] {issue.title}")
        extra = len(report.payload.issues) - 5
        if extra > 0:
            parts.append(f"- …and {extra} more")

    return "\n".join(parts)


def _report_to_markdown(report, evaluation, resource) -> str:
    """Render a Report (+ optional Evaluation, Resource) as a self-contained Markdown document."""
    name = resource.name if resource else report.resource_id
    parts: list[str] = [f"# Health Check: {name}", ""]

    parts.append(f"- **Generated:** {report.created_at}")
    if evaluation is not None:
        parts.append(f"- **Severity:** {evaluation.severity}")
    elif report.payload is not None:
        parts.append(f"- **Status:** {report.payload.status}")
    parts.append(f"- **Run type:** {report.run_type}")
    parts.append("")

    summary = None
    if evaluation is not None and evaluation.summary:
        summary = evaluation.summary
    elif report.payload is not None and report.payload.summary:
        summary = report.payload.summary
    if summary:
        parts.extend(["## Summary", "", summary, ""])

    payload = report.payload
    if payload is not None and payload.metrics:
        parts.extend(["## Metrics", "", "| Metric | Value |", "|--------|-------|"])
        for key, val in payload.metrics.items():
            parts.append(f"| {key} | {val} |")
        parts.append("")

    if report.payload_diff is not None:
        diff = report.payload_diff
        if diff.new:
            parts.extend(["## New Issues", ""])
            for item in diff.new:
                parts.append(f"- [{item.severity}] {item.title}")
            parts.append("")
        if diff.resolved:
            parts.extend(["## Resolved Issues", ""])
            for item in diff.resolved:
                parts.append(f"- [{item.severity}] {item.title}")
            parts.append("")

    if payload is not None and payload.issues:
        parts.extend(["## Issues", ""])
        for issue in payload.issues:
            parts.append(f"### [{issue.severity}] {issue.title}")
            parts.append("")
            if issue.scope:
                parts.append(f"**Scope:** {issue.scope}")
                parts.append("")
            if issue.evidence:
                parts.extend(["**Evidence:**", "", issue.evidence, ""])
            if issue.recommendation:
                parts.extend(["**Recommendation:**", "", issue.recommendation, ""])

    if report.content:
        parts.extend(["---", "", "## Full Report", "", report.content])

    return "\n".join(parts).rstrip() + "\n"


@router.get("/reports/{report_id}/export.md")
async def report_export_markdown(report_id: str, request: Request):
    """Download a report as a .md file. Read-only; viewers allowed."""
    store = request.app.state.store
    report = store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    evaluation = None
    for ev in store.get_recent_evaluations(report.resource_id, limit=20):
        if ev.report_id == report_id:
            evaluation = ev
            break

    resource = store.get_resource(report.resource_id)
    body = _report_to_markdown(report, evaluation, resource)

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", resource.name if resource else report.resource_id)
    date_str = report.created_at.strftime("%Y-%m-%d") if hasattr(report.created_at, "strftime") else "report"
    filename = f"report-{safe_name}-{date_str}.md"

    return PlainTextResponse(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Health Check triggers ─────────────────────────
