"""FastAPI application for Supervisor REST API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import Store
from ..engine import Engine
from ..scheduler import Scheduler
from ..templates import TEMPLATE_DIR_DEFAULT
from .dashboard import router as dashboard_router
from .routes import router as api_router

logger = logging.getLogger(__name__)


def create_app(
    db_path: str = ".supervisor/supervisor.db",
    template_dir: str = TEMPLATE_DIR_DEFAULT,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        store = Store(db_path)
        engine = Engine(store=store, template_dir=template_dir)
        scheduler = Scheduler(store=store, engine=engine)

        app.state.store = store
        app.state.engine = engine
        app.state.scheduler = scheduler

        # Start scheduler as background task
        scheduler_task = asyncio.create_task(scheduler.start_async())
        logger.info("API server started with embedded scheduler")

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

    app = FastAPI(
        title="Supervisor API",
        description="AI-powered infrastructure monitoring",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Static files (CSS, JS)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(api_router)       # /api/v1/* (JSON, requires API key)
    app.include_router(dashboard_router)  # /* (HTML, no auth for dashboard)

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
