"""Scheduler — runs discovery and health checks on their configured schedules.

Uses croniter for cron expression parsing. Runs as a blocking loop in its own
process via `supervisor run-scheduler`.

The scheduler creates Run records and executes them via the engine.
You can also trigger runs manually via CLI without the scheduler.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

from .db import Store
from .engine import Engine
from .models import Resource, RunType

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60
LOCK_FILE = ".supervisor/scheduler.lock"


class Scheduler:
    """Cron-based scheduler for monitoring runs."""

    def __init__(self, store: Store, engine: Engine):
        self.store = store
        self.engine = engine
        self._running = True
        self._lock_fd = None

    def _acquire_lock(self) -> bool:
        """Acquire an exclusive file lock to prevent multiple scheduler instances."""
        lock_path = Path(LOCK_FILE)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()) if hasattr(os, 'getpid') else "locked")
            self._lock_fd.flush()
            return True
        except (OSError, BlockingIOError):
            self._lock_fd.close()
            self._lock_fd = None
            return False

    def _release_lock(self) -> None:
        if self._lock_fd:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
            self._lock_fd = None

    def start(self) -> None:
        """Blocking loop. Checks every 60 seconds for due jobs."""
        if not self._acquire_lock():
            logger.error("Another scheduler instance is already running. Exiting.")
            return

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
        self._release_lock()

    def _get_due_jobs(self) -> list[tuple[Resource, RunType]]:
        """Check all resources' schedules in one batch (no N+1 queries)."""
        now = datetime.now(timezone.utc)
        due: list[tuple[Resource, RunType]] = []

        resources = self.store.list_resources()
        latest_runs = self.store.get_latest_runs_batch()

        for resource in resources:
            for run_type, schedule in [
                (RunType.DISCOVERY, resource.discovery_schedule),
                (RunType.HEALTH_CHECK, resource.health_check_schedule),
            ]:
                if not schedule or not schedule.enabled:
                    continue

                last_run = latest_runs.get((resource.id, str(run_type)))

                if last_run and last_run.completed_at:
                    cron = croniter(schedule.cron, last_run.completed_at)
                    next_run = cron.get_next(datetime)
                    if next_run.tzinfo is None:
                        next_run = next_run.replace(tzinfo=timezone.utc)
                    if now >= next_run:
                        due.append((resource, run_type))
                else:
                    due.append((resource, run_type))

        return due

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
