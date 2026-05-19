"""Shared trigger helper for run dispatch from both the REST API and the dashboard.

The HTTP-facing handlers (`web/routes.py:trigger_*` and
`web/dashboard/resources.py:trigger_*`) both need the same behavior:

1. Atomically create a PENDING ``Run`` for this resource — fail with HTTP 409
   if a PENDING/RUNNING run already exists.
2. Schedule the engine background task with that ``run_id`` so the engine
   updates the same row instead of creating a duplicate (the bug fixed by the
   v0.4.5 run-ID rewrite).
3. Return ``run_id`` so the caller can poll status.

Lives here, not in `web/routes.py`, so dashboard code doesn't import from the
API router module. Single source of truth.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..models import RunType

logger = logging.getLogger(__name__)


class TriggerResponse(BaseModel):
    """Body returned by every successful (202) trigger call."""

    ok: bool = True
    run_id: str


def trigger_run_or_409(store, engine, resource_id: str, run_type: RunType) -> JSONResponse:
    """Atomically queue a run for `resource_id` or raise 409.

    Returns a ``202 Accepted`` JSONResponse on success. The work has been
    queued in the background — the caller can poll ``GET /api/v1/runs/<id>``
    for status. The 200-status path was switched to 202 in 0.4.5.dev0; tests
    and clients should assert 202.

    Raises ``HTTPException(409)`` with a structured detail
    ``{error, active_run_id, hint}`` when a PENDING/RUNNING run is already in
    flight for the resource.
    """
    run = store.create_pending_run_if_no_active(resource_id, run_type)
    if run is None:
        active_id = store.get_active_run_id_for_resource(resource_id) or ""
        raise HTTPException(
            status_code=409,
            detail={
                "error": "run_in_flight",
                "active_run_id": active_id,
                "hint": "Poll GET /api/v1/runs/<active_run_id> to track the existing run.",
            },
        )

    async def _bg():
        try:
            if run_type == RunType.DISCOVERY:
                await engine.run_discovery_async(resource_id, run_id=run.id)
            else:
                await engine.run_health_check_async(resource_id, run_id=run.id)
        except Exception as e:
            logger.error(
                "Background run failed: resource=%s type=%s run_id=%s error=%s",
                resource_id,
                run_type,
                run.id,
                e,
            )

    asyncio.create_task(_bg())
    return JSONResponse(
        status_code=202,
        content=TriggerResponse(run_id=run.id).model_dump(),
    )
