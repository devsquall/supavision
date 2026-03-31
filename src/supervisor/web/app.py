"""FastAPI application for Supervisor REST API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    app.include_router(api_router)       # /api/v1/* (JSON, requires API key)
    app.include_router(dashboard_router)  # /* (HTML, no auth for dashboard)

    return app
