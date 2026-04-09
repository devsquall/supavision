"""Settings page — system info, API keys, blocklist, Claude check."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import _render

router = APIRouter()

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, new_key: str = ""):
    import os
    import shutil

    store = request.app.state.store
    entries = store.list_blocklist()
    api_keys = store.list_api_keys()

    # System info
    claude_path = shutil.which("claude")
    claude_version = None
    if claude_path:
        try:
            import subprocess
            r = subprocess.run(
                [claude_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            claude_version = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            pass

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
    stage_counts = store.count_work_items_by_stage()
    finding_count = sum(stage_counts.values())

    # Notification history
    notifications = store.list_notifications(limit=20)
    resource_map = {r.id: r.name for r in resources}
    for n in notifications:
        n["resource_name"] = resource_map.get(n["resource_id"], "")

    return _render(request, "settings.html", {
        "entries": entries,
        "api_keys": api_keys,
        "new_key": new_key,
        "notifications": notifications,
        "system_info": {
            "claude_version": claude_version,
            "db_size": db_size,
            "resource_count": len(resources),
            "finding_count": finding_count,
        },
    })


@router.post("/settings/api-keys")
async def settings_create_api_key(request: Request):
    from fastapi.responses import RedirectResponse

    from ..auth import generate_api_key

    store = request.app.state.store
    form = await request.form()
    label = form.get("label", "").strip()

    if not label:
        # Redirect back without creating — label is required
        return RedirectResponse(url="/settings", status_code=303)

    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label=label)

    # Redirect back to settings with the raw key displayed once
    return RedirectResponse(
        url=f"/settings?new_key={raw_key}", status_code=303
    )


@router.post("/settings/api-keys/{key_id}/revoke")
async def settings_revoke_api_key(key_id: str, request: Request):
    from fastapi.responses import HTMLResponse

    store = request.app.state.store
    if store.revoke_api_key(key_id):
        # Return empty content — HTMX removes the row
        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", status_code=200)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/settings", status_code=303)
    raise HTTPException(status_code=404, detail="Key not found")


@router.delete("/settings/blocklist/{entry_id}")
async def settings_blocklist_delete(entry_id: str, request: Request):
    from fastapi.responses import HTMLResponse

    store = request.app.state.store
    if store.delete_blocklist_entry(entry_id):
        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", status_code=200)
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Entry not found")


@router.post("/settings/check-claude")
async def settings_check_claude(request: Request):
    """Check if Claude CLI is now available and re-initialize engine if so."""
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

