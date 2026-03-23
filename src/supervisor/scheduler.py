"""Scheduler — runs discovery and health checks on their configured schedules.

Uses croniter for cron expression parsing. Runs as a blocking loop in its own
process via `supervisor run-scheduler`.

The scheduler creates Run records and executes them via the engine.
You can also trigger runs manually via CLI without the scheduler.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from croniter import croniter

from .db import Store
from .engine import Engine
from .models import Resource, RunType

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60  # How often to check for due jobs


class Scheduler:
    """Cron-based scheduler for monitoring runs."""

    def __init__(self, store: Store, engine: Engine):
        self.store = store
        self.engine = engine
        self._running = True

    def start(self) -> None:
        """Blocking loop. Checks every 60 seconds for due jobs."""
        logger.info("Scheduler started. Checking every %ds for due jobs.", CHECK_INTERVAL_SECONDS)

        while self._running:
            try:
                due_jobs = self._get_due_jobs()
                for resource, run_type in due_jobs:
                    self._execute_run(resource, run_type)
            except Exception as e:
                logger.error("Scheduler error: %s", e)

            time.sleep(CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._running = False

    def _get_due_jobs(self) -> list[tuple[Resource, RunType]]:
        """Check all resources' schedules against current time and last run."""
        now = datetime.now(timezone.utc)
        due: list[tuple[Resource, RunType]] = []

        resources = self.store.list_resources()
        for resource in resources:
            # Check discovery schedule
            if resource.discovery_schedule and resource.discovery_schedule.enabled:
                if self._is_due(resource, RunType.DISCOVERY, now):
                    due.append((resource, RunType.DISCOVERY))

            # Check health check schedule
            if resource.health_check_schedule and resource.health_check_schedule.enabled:
                if self._is_due(resource, RunType.HEALTH_CHECK, now):
                    due.append((resource, RunType.HEALTH_CHECK))

        return due

    def _is_due(self, resource: Resource, run_type: RunType, now: datetime) -> bool:
        """Check if a run is due based on cron schedule and last run time."""
        schedule = (
            resource.discovery_schedule
            if run_type == RunType.DISCOVERY
            else resource.health_check_schedule
        )
        if not schedule or not schedule.enabled:
            return False

        # Find last completed run of this type
        last_run = self.store.get_latest_run(resource.id, run_type)

        if last_run and last_run.completed_at:
            # Calculate next run time after last completion
            cron = croniter(schedule.cron, last_run.completed_at)
            next_run = cron.get_next(datetime)
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
            return now >= next_run
        else:
            # Never run before — due immediately
            return True

    def _execute_run(self, resource: Resource, run_type: RunType) -> None:
        """Execute a single scheduled run."""
        logger.info("Executing scheduled %s for %s", run_type, resource.name)
        try:
            if run_type == RunType.DISCOVERY:
                self.engine.run_discovery(resource.id)
            else:
                self.engine.run_health_check(resource.id)
        except Exception as e:
            logger.error(
                "Scheduled %s failed for %s: %s", run_type, resource.name, e
            )
