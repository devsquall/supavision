"""Settings page — system info, API keys, Claude check."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import _render, _require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, new_key: str = ""):
    import os
    import shutil

    store = request.app.state.store
    api_keys = store.list_api_keys()

    # System info
    claude_path = shutil.which("claude")
    claude_version = None
    if claude_path:
        try:
            import subprocess

            r = subprocess.run(
                [claude_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            claude_version = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            # claude --version timed out or threw; show "unknown" but log so
            # the operator can see what went wrong in supavision serve logs.
            logger.exception("Failed to read 'claude --version'")

    db_path = store.db_path
    db_size = "unknown"
    try:
        size_bytes = os.path.getsize(str(db_path))
        if size_bytes < 1024 * 1024:
            db_size = f"{size_bytes / 1024:.0f} KB"
        else:
            db_size = f"{size_bytes / 1024 / 1024:.1f} MB"
    except OSError:
        pass

    resources = store.list_resources()

    # Notification history
    notifications = store.list_notifications(limit=20)
    resource_map = {r.id: r.name for r in resources}
    for n in notifications:
        n["resource_name"] = resource_map.get(n["resource_id"], "")

    # Scheduler status (P2 #14)
    scheduler_status: dict[str, object] = {"running": False, "details": "not started"}
    try:
        from ...scheduler import get_scheduler_status

        scheduler_status = get_scheduler_status()
    except Exception as e:
        scheduler_status = {"running": False, "details": f"error: {e}"}

    # Claude auth status (file-based check — never invokes the CLI)
    try:
        from ..._auth_check import check_claude_auth

        claude_auth_ok, claude_auth_detail = check_claude_auth()
    except Exception:
        logger.exception("check_claude_auth failed unexpectedly")
        claude_auth_ok, claude_auth_detail = False, "auth check failed"

    # Setup checklist (P2 #14) — derived from current system state.
    has_admin_user = store.count_users() > 0
    has_api_key = len(api_keys) > 0
    has_any_resource = len(resources) > 0
    has_successful_run = False
    for r in resources:
        runs = store.get_runs(r.id, limit=1)
        if runs and str(runs[0].status) == "completed":
            has_successful_run = True
            break

    setup_checklist = [
        {"label": "Claude CLI installed", "done": claude_path is not None},
        {"label": "Claude CLI authenticated", "done": claude_auth_ok},
        {"label": "Admin user created", "done": has_admin_user},
        {"label": "API key created (for CLI / external clients)", "done": has_api_key},
        {"label": "At least one resource added", "done": has_any_resource},
        {"label": "At least one successful run", "done": has_successful_run},
        {"label": "Scheduler running", "done": bool(scheduler_status.get("running"))},
    ]

    return _render(
        request,
        "settings.html",
        {
            "api_keys": api_keys,
            "new_key": new_key,
            "notifications": notifications,
            "system_info": {
                "claude_version": claude_version,
                "claude_auth_ok": claude_auth_ok,
                "claude_auth_detail": claude_auth_detail,
                "scheduler_running": bool(scheduler_status.get("running")),
                "scheduler_detail": scheduler_status.get("details", ""),
                "db_size": db_size,
                "resource_count": len(resources),
            },
            "setup_checklist": setup_checklist,
        },
    )


@router.get("/settings/audit-log", response_class=HTMLResponse)
async def settings_audit_log(request: Request, page: int = 1, event: str = ""):
    """Auth audit log viewer — admin only.

    Surfaces the `auth_audit_log` table (login successes/failures, user
    creates, role changes, etc.). Previously this data was written on every
    auth event but never displayed; operators had to read sqlite manually.
    """
    _require_admin(request)
    store = request.app.state.store

    per_page = 50
    page = max(1, int(page or 1))
    offset = (page - 1) * per_page

    event_filter = event.strip() or None
    rows, total = store.list_auth_events_paginated(
        limit=per_page,
        offset=offset,
        event=event_filter,
    )

    # Known events (kept short — read from existing audit entries if you add new ones).
    event_choices = [
        "login_success",
        "login_failure",
        "logout",
        "user_created",
        "user_activated",
        "user_deactivated",
        "role_changed",
        "password_changed",
        "session_revoked",
    ]

    return _render(
        request,
        "settings_audit_log.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "event_filter": event_filter,
            "event_choices": event_choices,
        },
    )


@router.post("/settings/api-keys")
async def settings_create_api_key(request: Request):
    _require_admin(request)
    from fastapi.responses import RedirectResponse

    from ..auth import generate_api_key

    store = request.app.state.store
    form = await request.form()
    label = form.get("label", "").strip()

    if not label:
        # Redirect back without creating — label is required
        return RedirectResponse(url="/settings", status_code=303)

    key_id, raw_key, key_hash = generate_api_key()
    user = getattr(request.state, "current_user", None)
    role = user.role if user else "admin"
    store.save_api_key(key_id, key_hash, label=label, role=role)

    # Redirect back to settings with the raw key displayed once
    return RedirectResponse(url=f"/settings?new_key={raw_key}", status_code=303)


@router.post("/settings/api-keys/{key_id}/revoke")
async def settings_revoke_api_key(key_id: str, request: Request):
    _require_admin(request)
    from fastapi.responses import HTMLResponse

    store = request.app.state.store
    if store.revoke_api_key(key_id):
        # Return empty content — HTMX removes the row
        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", status_code=200)
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/settings", status_code=303)
    raise HTTPException(status_code=404, detail="Key not found")


@router.post("/settings/check-claude")
async def settings_check_claude(request: Request):
    """Check if Claude CLI is now available and re-initialize engine if so."""
    _require_admin(request)
    import shutil

    from ...engine import Engine
    from ...templates import TEMPLATE_DIR_DEFAULT

    claude_path = shutil.which("claude")
    if not claude_path:
        return {"ok": False, "message": "Claude CLI not found in PATH."}

    if request.app.state.engine is not None:
        return {"ok": True, "message": "Infrastructure engine already running."}

    try:
        store = request.app.state.store
        engine = Engine(store=store, template_dir=TEMPLATE_DIR_DEFAULT)
        request.app.state.engine = engine
        return {"ok": True, "message": "Claude CLI detected. Infrastructure monitoring enabled."}
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}
