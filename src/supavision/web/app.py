"""FastAPI application for Supavision REST API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..agent_runner import start_runner, stop_runner
from ..config import DASHBOARD_PASSWORD, DASHBOARD_USER, SESSION_IDLE_MINUTES
from ..db import Store
from ..engine import Engine
from ..models import User
from ..scheduler import Scheduler
from ..templates import TEMPLATE_DIR_DEFAULT
from ..web.auth import hash_password
from .dashboard import router as dashboard_router
from .routes import health_router, router as api_router

logger = logging.getLogger(__name__)


def create_app(
    db_path: str = ".supavision/supavision.db",
    template_dir: str = TEMPLATE_DIR_DEFAULT,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        store = Store(db_path)

        # Infrastructure engine (requires Claude CLI — optional)
        try:
            engine = Engine(store=store, template_dir=template_dir)
        except RuntimeError as e:
            logger.warning("Infrastructure engine unavailable: %s", e)
            logger.warning(
                "Codebase scanning will still work. "
                "Install Claude CLI for infrastructure monitoring."
            )
            engine = None

        scheduler = Scheduler(store=store, engine=engine)

        app.state.store = store
        app.state.engine = engine
        app.state.scheduler = scheduler

        # Start scheduler as background task
        scheduler_task = asyncio.create_task(scheduler.start_async())

        # Backward compatibility: auto-create admin from SUPAVISION_PASSWORD
        if DASHBOARD_PASSWORD and store.count_users() == 0:
            admin_email = f"{DASHBOARD_USER}@localhost"
            admin = User(
                email=admin_email,
                password_hash=hash_password(DASHBOARD_PASSWORD),
                name=DASHBOARD_USER,
                role="admin",
            )
            store.create_user(admin)
            store.log_auth_event("user_created", user_id=admin.id, email=admin_email, detail="auto-created from SUPAVISION_PASSWORD")
            logger.info("Auto-created admin user from SUPAVISION_PASSWORD (email=%s)", admin_email)

        # Start agent runner for codebase jobs
        start_runner(store)
        logger.info("API server started with scheduler + agent runner")

        yield

        # Shutdown
        stop_runner()
        scheduler.stop()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        store.close()
        logger.info("API server shut down")

    app = FastAPI(
        title="Supavision API",
        description="AI-powered infrastructure monitoring",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Static files (CSS, JS)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Session-based auth middleware ──────────────────────────────────
    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        # Skip API routes, static files, and the login page itself
        if request.url.path.startswith(("/api/v1/", "/static/", "/login")):
            return await call_next(request)

        store: Store = request.app.state.store

        # Check if any users exist — if not, allow unauthenticated access
        # (first-time setup before create-admin has been run)
        if store.count_users() == 0:
            request.state.csrf_token = ""
            request.state.current_user = None
            request.state.is_admin = True  # No users = unrestricted access
            return await call_next(request)

        # Check session cookie
        session_id = request.cookies.get("session_id")
        if session_id:
            session = store.get_session(session_id)
            if session:
                user = store.get_user(session.user_id)
                if user and user.is_active:
                    request.state.current_user = user
                    request.state.is_admin = user.role == "admin"
                    request.state.csrf_token = session.csrf_token

                    # CSRF validation on mutating requests
                    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                        # Accept from header (HTMX) or form body (no-JS fallback)
                        token = request.headers.get("x-csrf-token", "")
                        if not token:
                            body = await request.body()
                            if b"csrf_token=" in body:
                                from urllib.parse import parse_qs
                                parsed = parse_qs(body.decode("utf-8", errors="replace"))
                                token = parsed.get("csrf_token", [""])[0]
                            request._body = body  # Re-wrap for downstream
                        if not token or not hmac.compare_digest(token, session.csrf_token):
                            return Response(status_code=403, content="CSRF validation failed")

                    # Touch session for idle timeout tracking
                    store.touch_session(session_id)
                    return await call_next(request)

        # Not authenticated — redirect to login
        from fastapi.responses import RedirectResponse
        next_url = request.url.path
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    logger.info("Session-based auth enabled")

    app.include_router(health_router)     # /api/v1/health (no auth, for healthchecks)
    app.include_router(api_router)        # /api/v1/* (JSON, requires API key)
    app.include_router(dashboard_router)  # /* (HTML, basic auth if configured)

    # Custom error pages (branded, not default FastAPI JSON)
    _templates_dir = Path(__file__).parent / "templates"
    _error_templates = Jinja2Templates(directory=str(_templates_dir))

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return HTMLResponse(content='{"detail":"Not found"}', status_code=404, media_type="application/json")
        return _error_templates.TemplateResponse(request, "error.html", {
            "status_code": 404, "message": "Page not found.",
        }, status_code=404)

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        if request.url.path.startswith("/api/"):
            body = '{"detail":"Internal server error"}'
            return HTMLResponse(content=body, status_code=500, media_type="application/json")
        return _error_templates.TemplateResponse(request, "error.html", {
            "status_code": 500, "message": "Something went wrong. Please try again.",
        }, status_code=500)

    return app
