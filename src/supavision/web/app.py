"""FastAPI application for Supavision REST API."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from .._auth_check import check_claude_auth
from ..config import DASHBOARD_PASSWORD, DASHBOARD_USER
from ..db import Store
from ..engine import Engine
from ..models import User
from ..scheduler import Scheduler
from ..templates import TEMPLATE_DIR_DEFAULT
from ..web.auth import hash_password, validate_password_strength
from .dashboard import router as dashboard_router
from .routes import health_router
from .routes import router as api_router

logger = logging.getLogger(__name__)


def create_app(
    db_path: str | None = None,
    template_dir: str = TEMPLATE_DIR_DEFAULT,
) -> FastAPI:
    """Create and configure the FastAPI application.

    `db_path` resolution order:
    1. Explicit ``db_path=`` kwarg (used by tests).
    2. ``SUPAVISION_DB_PATH`` environment variable (matches the CLI's fallback
       and the MCP server's contract — see `cli.py:_get_store` and `mcp.py`).
    3. ``.supavision/supavision.db`` relative to the working directory.

    Without step (2), ``uvicorn supavision.web.app:create_app --factory`` (the
    documented dev command) silently used the local default DB regardless of
    ``$SUPAVISION_DB_PATH``, which surprised anyone running it outside the
    project root.
    """
    if db_path is None:
        db_path = os.environ.get("SUPAVISION_DB_PATH") or ".supavision/supavision.db"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        try:
            store = Store(db_path)
        except Exception as e:
            logger.critical("Failed to initialize database at %s: %s", db_path, e)
            raise RuntimeError(f"Database initialization failed: {e}") from e

        # Infrastructure engine (requires Claude CLI — optional)
        try:
            engine = Engine(store=store, template_dir=template_dir)
        except RuntimeError as e:
            logger.warning("Infrastructure engine unavailable: %s", e)
            engine = None

        scheduler = Scheduler(store=store, engine=engine)

        app.state.store = store
        app.state.engine = engine
        app.state.scheduler = scheduler

        # Start scheduler as background task
        scheduler_task = asyncio.create_task(scheduler.start_async())

        # Backward compatibility: auto-create admin from SUPAVISION_PASSWORD
        if DASHBOARD_PASSWORD and store.count_users() == 0:
            strength_error = validate_password_strength(DASHBOARD_PASSWORD)
            if strength_error:
                logger.warning(
                    "SUPAVISION_PASSWORD does not meet strength requirements (%s). "
                    "Switch to 'supavision create-admin' for secure account creation.",
                    strength_error,
                )
            admin_email = f"{DASHBOARD_USER}@localhost"
            admin = User(
                email=admin_email,
                password_hash=hash_password(DASHBOARD_PASSWORD),
                name=DASHBOARD_USER,
                role="admin",
            )
            store.create_user(admin)
            store.log_auth_event(
                "user_created",
                user_id=admin.id,
                email=admin_email,
                detail="auto-created from SUPAVISION_PASSWORD",
            )
            logger.warning(
                "SUPAVISION_PASSWORD is deprecated. Use 'supavision create-admin' instead. "
                "This auto-migration will be removed in a future version."
            )
            logger.info("Auto-created admin user from SUPAVISION_PASSWORD (email=%s)", admin_email)

        # Warn early if Claude CLI backend is not authenticated
        if os.environ.get("SUPAVISION_BACKEND", "claude_cli") == "claude_cli" and engine is not None:
            auth_ok, auth_detail = check_claude_auth()
            if not auth_ok:
                logger.warning(
                    "Claude CLI is not authenticated (%s). "
                    "Discovery and health check runs will fail. "
                    "Run 'supavision setup' or 'claude login' to authenticate.",
                    auth_detail,
                )

        logger.info("API server started with scheduler")

        yield

        # Shutdown
        scheduler.stop()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        store.close()
        logger.info("API server shut down")

    # Disable FastAPI's built-in /docs, /redoc, /openapi.json — we mount them
    # ourselves below behind an admin role check. Previously, any authenticated
    # session user (viewer included) could browse the full API surface at
    # /docs and learn which admin-only mutating endpoints exist. Restricting
    # to admin reduces information disclosure without breaking the workflow
    # for the user who actually needs the docs (an operator with an API key).
    app = FastAPI(
        title="Supavision API",
        description="AI-powered infrastructure monitoring",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Static files (CSS, JS)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Session-based auth middleware ──────────────────────────────────
    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        # Skip API routes, static files, login, and public marketing page
        if request.url.path.startswith(("/api/v1/", "/static/", "/login", "/landing")):
            return await call_next(request)

        store: Store = request.app.state.store

        # First-run mode: no admin user exists yet. Refuse to serve the
        # dashboard until `supavision create-admin` has been run. Previously
        # the middleware logged CRITICAL and served the dashboard with
        # is_admin=True, which meant exposing the port before bootstrap
        # silently granted admin to anyone who hit it. Now: redirect every
        # browser request to /landing, which renders a "run create-admin"
        # instructional banner. /login is reachable but no credentials work
        # because no user exists yet.
        if store.count_users() == 0:
            logger.critical("NO USERS CONFIGURED — dashboard locked until `supavision create-admin` runs.")
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/landing?setup=1", status_code=302)

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

        # Not authenticated — redirect.
        # Root "/" hits the public marketing page first.
        # Deep links go to login?next=... so users land where they intended.
        from fastapi.responses import RedirectResponse

        if request.url.path == "/":
            return RedirectResponse(url="/landing", status_code=302)
        next_url = request.url.path
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    logger.info("Session-based auth enabled")

    app.include_router(health_router)  # /api/v1/health (no auth, for healthchecks)
    app.include_router(api_router)  # /api/v1/* (JSON, requires API key)
    app.include_router(dashboard_router)  # /* (HTML, session auth)

    # ── Admin-only OpenAPI docs ────────────────────────────────────────
    # Re-mount /docs, /redoc, /openapi.json with an admin role check.
    # The session middleware above already gates these behind login; this
    # layer adds the role check so viewers can't browse the API surface.
    from fastapi import HTTPException
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

    def _require_admin_state(request: Request) -> None:
        if not getattr(request.state, "is_admin", False):
            raise HTTPException(status_code=403, detail="Admin access required")

    @app.get("/openapi.json", include_in_schema=False)
    async def custom_openapi(request: Request):
        _require_admin_state(request)
        return app.openapi()

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui(request: Request):
        _require_admin_state(request)
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — API docs",
        )

    @app.get("/redoc", include_in_schema=False)
    async def custom_redoc(request: Request):
        _require_admin_state(request)
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — API docs",
        )

    # Custom error pages (branded, not default FastAPI JSON)
    _templates_dir = Path(__file__).parent / "templates"
    _error_templates = Jinja2Templates(directory=str(_templates_dir))

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return HTMLResponse(content='{"detail":"Not found"}', status_code=404, media_type="application/json")
        return _error_templates.TemplateResponse(
            request,
            "error.html",
            {
                "status_code": 404,
                "message": "Page not found.",
            },
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        if request.url.path.startswith("/api/"):
            body = '{"detail":"Internal server error"}'
            return HTMLResponse(content=body, status_code=500, media_type="application/json")
        return _error_templates.TemplateResponse(
            request,
            "error.html",
            {
                "status_code": 500,
                "message": "Something went wrong. Please try again.",
            },
            status_code=500,
        )

    return app
