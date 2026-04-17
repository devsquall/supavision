"""Authentication routes — login, logout, profile, user management."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from . import _check_rate_limit, _render, templates

router = APIRouter()


# ── Login / Logout ────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    """Show login form."""
    from urllib.parse import urlparse
    parsed = urlparse(next)
    if parsed.scheme or parsed.netloc or not next.startswith("/"):
        next = "/"
    if hasattr(request.state, "current_user") and request.state.current_user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=next)
    return templates.TemplateResponse(request, "login.html", {"next_url": next})


@router.post("/login")
async def login_submit(request: Request):
    """Validate credentials and create session."""
    from fastapi.responses import RedirectResponse

    from ...config import SESSION_COOKIE_SECURE, SESSION_HOURS
    from ...models import Session
    from ...web.auth import verify_password

    store = request.app.state.store
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    next_url = form.get("next", "/")

    # Validate redirect URL
    from urllib.parse import urlparse
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/"):
        next_url = "/"

    # Rate limit (configurable via SUPAVISION_RATE_LIMIT_LOGIN, default 5/min per IP)
    from ...config import RATE_LIMIT_LOGIN
    if not _check_rate_limit(request.client.host, max_per_minute=RATE_LIMIT_LOGIN):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Too many login attempts. Try again in a minute.",
            "next_url": next_url,
        }, status_code=429)

    # Validate credentials — generic error prevents user enumeration
    user = store.get_user_by_email(email)
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        store.log_auth_event("login_failure", email=email, ip_address=request.client.host)
        return templates.TemplateResponse(request, "login.html", {
            "error": "Invalid email or password.",
            "next_url": next_url,
        }, status_code=401)

    # Create session
    from datetime import timedelta
    session = Session(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS),
        ip_address=request.client.host or "",
        user_agent=request.headers.get("user-agent", "")[:200],
    )
    store.create_session(session)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    store.update_user(user)

    store.log_auth_event("login_success", user_id=user.id, email=email, ip_address=request.client.host)

    response = RedirectResponse(url=next_url, status_code=302)
    response.set_cookie(
        "session_id", session.id,
        httponly=True, samesite="lax",
        secure=SESSION_COOKIE_SECURE,
        max_age=SESSION_HOURS * 3600,
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """Revoke session and clear cookie. POST with CSRF protection."""
    from fastapi.responses import RedirectResponse

    store = request.app.state.store
    session_id = request.cookies.get("session_id")
    if session_id:
        user = getattr(request.state, "current_user", None)
        store.revoke_session(session_id)
        store.log_auth_event("logout", user_id=user.id if user else None, ip_address=request.client.host)

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_id")
    return response


# ── User Management (admin only) ──────────────────────────────────


@router.get("/settings/users", response_class=HTMLResponse)
async def settings_users(request: Request):
    """User management page — admin only."""
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    store = request.app.state.store
    users = store.list_users()
    audit_log = store.get_auth_audit_log(limit=20)
    return _render(request, "settings_users.html", {"users": users, "audit_log": audit_log})

@router.post("/settings/users/create")
async def create_user(request: Request):
    """Create a new user — admin only."""
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    from ...models import User
    from ...web.auth import hash_password, validate_password_strength

    store = request.app.state.store
    form = await request.form()
    email = form.get("email", "").strip()
    name = form.get("name", "").strip()
    password = form.get("password", "")
    role = form.get("role", "viewer")

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    if store.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Email already exists")
    error = validate_password_strength(password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if role not in ("admin", "viewer"):
        role = "viewer"

    user = User(email=email, password_hash=hash_password(password), name=name or email.split("@")[0], role=role)
    store.create_user(user)
    store.log_auth_event(
        "user_created", user_id=user.id, email=email,
        detail=f"role={role}", ip_address=request.client.host,
    )

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/settings/users", status_code=302)

@router.post("/settings/users/{user_id}/toggle")
async def toggle_user(user_id: str, request: Request):
    """Activate/deactivate a user — admin only."""
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    store = request.app.state.store
    user = store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = not user.is_active
    store.update_user(user)
    if not user.is_active:
        store.revoke_user_sessions(user_id)
    event = "user_activated" if user.is_active else "user_deactivated"
    store.log_auth_event(event, user_id=user_id, email=user.email, ip_address=request.client.host)
    return Response(status_code=204)

@router.post("/settings/users/{user_id}/role")
async def change_role(user_id: str, request: Request):
    """Change a user's role — admin only."""
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    store = request.app.state.store
    user = store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    form = await request.form()
    new_role = form.get("role", "viewer")
    if new_role not in ("admin", "viewer"):
        new_role = "viewer"
    old_role = user.role
    user.role = new_role
    store.update_user(user)
    store.revoke_user_sessions(user_id)  # Force re-login to pick up new role
    store.log_auth_event(
        "role_changed", user_id=user_id, email=user.email,
        detail=f"{old_role} → {new_role}", ip_address=request.client.host,
    )
    return Response(status_code=204)


# ── Profile routes (self-service) ──────────────────────────────────


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """Self-service profile page — view account info, change password, manage sessions."""
    user = getattr(request.state, "current_user", None)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")
    store = request.app.state.store
    sessions = store.get_user_sessions(user.id)
    session_id = request.cookies.get("session_id", "")
    return _render(request, "profile.html", {
        "profile_user": user,
        "sessions": sessions,
        "current_session_id": session_id,
    })


@router.post("/profile/password")
async def change_password(request: Request):
    """Change own password — validates current password, revokes all sessions."""
    from ...web.auth import hash_password, validate_password_strength, verify_password

    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401)

    store = request.app.state.store
    form = await request.form()
    current = form.get("current_password", "")
    new_pw = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    # Validate current password
    if not verify_password(current, user.password_hash):
        sessions = store.get_user_sessions(user.id)
        return _render(request, "profile.html", {
            "profile_user": user, "sessions": sessions,
            "current_session_id": request.cookies.get("session_id", ""),
            "password_error": "Current password is incorrect.",
        })

    # Validate new password
    if new_pw != confirm:
        sessions = store.get_user_sessions(user.id)
        return _render(request, "profile.html", {
            "profile_user": user, "sessions": sessions,
            "current_session_id": request.cookies.get("session_id", ""),
            "password_error": "New passwords do not match.",
        })

    error = validate_password_strength(new_pw)
    if error:
        sessions = store.get_user_sessions(user.id)
        return _render(request, "profile.html", {
            "profile_user": user, "sessions": sessions,
            "current_session_id": request.cookies.get("session_id", ""),
            "password_error": error,
        })

    # Update password + revoke all sessions (forces re-login everywhere)
    user.password_hash = hash_password(new_pw)
    store.update_user(user)
    store.revoke_user_sessions(user.id)
    store.log_auth_event("password_changed", user_id=user.id, email=user.email, ip_address=request.client.host)

    # Redirect to login (all sessions revoked, including current)
    from fastapi.responses import RedirectResponse
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_id")
    return response


@router.post("/profile/name")
async def update_profile_name(request: Request):
    """Update the current user's display name."""
    session_user = getattr(request.state, "current_user", None)
    if not session_user:
        raise HTTPException(status_code=401)
    form = await request.form()
    new_name = form.get("name", "").strip()[:100]
    store = request.app.state.store
    # Re-fetch to avoid overwriting concurrent admin changes (role, email, etc.)
    user = store.get_user(session_user.id)
    if not user:
        raise HTTPException(status_code=404)
    if new_name == user.name:
        return Response(status_code=204)
    user.name = new_name
    store.update_user(user)
    store.log_auth_event(
        "profile_name_changed",
        user_id=user.id, email=user.email,
        ip_address=request.client.host if request.client else None,
    )
    resp = Response(status_code=204)
    resp.headers["HX-Refresh"] = "true"  # refresh so sidebar/topbar pick up new name
    return resp


@router.post("/profile/sessions/{session_id}/revoke")
async def revoke_own_session(session_id: str, request: Request):
    """Revoke one of your own sessions."""
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401)

    store = request.app.state.store
    session = store.get_session(session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    store.revoke_session(session_id)
    store.log_auth_event("session_revoked", user_id=user.id, email=user.email, ip_address=request.client.host)
    return Response(status_code=204)
