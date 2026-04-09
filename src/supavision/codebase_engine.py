"""Codebase Engine — orchestrates scan, evaluate, implement, and scout for codebase resources.

Two-phase discovery:
  1. Deterministic regex scan (scanner.py, no LLM, zero cost)
  2. Optional agent-driven scout (Claude explores the codebase for deeper issues)

This is a shared-lane orchestrator — it produces Lane 2 WorkItem/AgentJob
records and also creates Lane 1 Reports/Evaluations for health badge display.

For resource-level health summaries (Lane 1), the codebase_engine creates
a Report with aggregate scan stats. This Report feeds the existing
resource dashboard (health badge, trend sparkline). The actual findings
live in the work_items table (Lane 2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .blocklist import Blocklist
from .db import Store
from .models import (
    AgentJob,
    Evaluation,
    FindingSeverity,
    FindingStage,
    Report,
    Run,
    RunStatus,
    RunType,
    Severity,
)
from .scanner import scan_directory

logger = logging.getLogger(__name__)


class CodebaseEngine:
    """Execution engine for codebase resource operations.

    Separate from the infrastructure Engine — different trust model,
    different execution patterns (scanner + agent vs. template + SSH).
    """

    def __init__(self, store: Store):
        self.store = store

    def run_scan(self, resource_id: str) -> Run:
        """Execute a codebase scan: regex patterns -> findings -> report.

        This is the Lane 2 equivalent of discovery for infrastructure resources.
        """
        resource = self.store.get_resource(resource_id)
        if not resource:
            raise ValueError(f"Resource {resource_id} not found")

        if resource.resource_type != "codebase":
            raise ValueError(
                f"Resource {resource.name} is type '{resource.resource_type}', "
                f"not 'codebase'. Use the infrastructure engine for this resource."
            )

        work_dir = resource.config.get("path", "")
        if not work_dir or not Path(work_dir).is_dir():
            raise ValueError(f"Resource path not found: {work_dir}")

        # Create run
        run = Run(
            resource_id=resource_id,
            run_type=RunType.SCAN,
            status=RunStatus.RUNNING,
        )
        run.started_at = datetime.now(timezone.utc)
        self.store.save_run(run)

        try:
            # Load blocklist for false-positive filtering
            bl_entries = self.store.list_blocklist()
            blocklist = Blocklist(bl_entries) if bl_entries else None

            # Get existing signatures for dedup
            existing_items, _ = self.store.list_work_items(
                resource_id=resource_id, source="scanner", per_page=10000,
            )
            existing_sigs = {item.dedup_signature for item in existing_items}

            # Get last scan time for incremental scanning
            last_scan_at = None
            latest_run = self.store.get_latest_run(resource_id, RunType.SCAN)
            if latest_run and latest_run.completed_at:
                last_scan_at = latest_run.completed_at

            # Run scanner
            result, findings = scan_directory(
                resource_id=resource_id,
                directory=work_dir,
                run_id=run.id,
                blocklist=blocklist,
                existing_signatures=existing_sigs,
                last_scan_at=last_scan_at,
            )

            # Save findings as WorkItems (Lane 2)
            for f in findings:
                self.store.save_work_item(f)

            # Create aggregate Report (Lane 1) — scan summary for health display
            report = Report(
                resource_id=resource_id,
                run_type=RunType.SCAN,
                content=(
                    f"Scan completed: {result.summary}\n\n"
                    f"Total pattern hits: {result.total_hits}\n"
                    f"Findings created: {result.findings_created}\n"
                    f"Findings dismissed (blocklist): {result.findings_dismissed}\n"
                    f"High: {result.high_hits}, "
                    f"Medium: {result.medium_hits}, "
                    f"Low: {result.low_hits}"
                ),
            )
            self.store.save_report(report)

            # Create resource-level Evaluation (Lane 1) for health badge
            if result.high_hits > 0:
                health = Severity.WARNING
                should_alert = True
            elif result.findings_created > 0:
                health = Severity.WARNING
                should_alert = False
            else:
                health = Severity.HEALTHY
                should_alert = False

            critical_count = sum(
                1 for f in findings
                if f.severity == FindingSeverity.CRITICAL
            )
            if critical_count > 0:
                health = Severity.CRITICAL
                should_alert = True

            evaluation = Evaluation(
                report_id=report.id,
                resource_id=resource_id,
                severity=health,
                summary=f"Scan: {result.summary}",
                should_alert=should_alert,
                strategy_used="keyword",
            )
            self.store.save_evaluation(evaluation)

            # Complete run
            run.status = RunStatus.COMPLETED
            run.report_id = report.id
            run.evaluation_id = evaluation.id
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)

            # Fire alerts for critical/high findings
            if evaluation.should_alert:
                try:
                    import asyncio

                    from .notifications import send_alert
                    try:
                        loop = asyncio.get_running_loop()
                        # Already in async context — schedule as task
                        loop.create_task(
                            send_alert(resource, report, evaluation)
                        )
                    except RuntimeError:
                        # Not in async context (CLI/sync) — run directly
                        asyncio.run(
                            send_alert(resource, report, evaluation)
                        )
                except Exception as e:
                    logger.warning("Alert dispatch failed for %s: %s", resource.name, e)

            logger.info(
                "Scan completed for %s: %s", resource.name, result.summary
            )
            return run

        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)[:500]
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)
            raise

    def create_evaluate_job(
        self, work_item_id: str, resource_id: str
    ) -> AgentJob:
        """Create an evaluation AgentJob for a work item.

        The AgentRunner picks this up and runs Claude Code with read-only tools.
        """
        item = self.store.get_work_item(work_item_id)
        if not item:
            raise ValueError(f"Work item {work_item_id} not found")

        job = AgentJob(
            work_item_id=work_item_id,
            resource_id=resource_id,
            job_type="evaluate",
        )
        self.store.save_agent_job(job)
        return job

    def create_implement_job(
        self, work_item_id: str, resource_id: str
    ) -> AgentJob:
        """Create an implementation AgentJob for a work item.

        The AgentRunner picks this up and runs Claude Code with full tool access.
        """
        item = self.store.get_work_item(work_item_id)
        if not item:
            raise ValueError(f"Work item {work_item_id} not found")

        if item.stage != FindingStage.APPROVED:
            raise ValueError(
                f"Work item must be in APPROVED stage to implement "
                f"(currently: {item.stage})"
            )

        job = AgentJob(
            work_item_id=work_item_id,
            resource_id=resource_id,
            job_type="implement",
        )
        self.store.save_agent_job(job)
        return job

    def create_scout_job(
        self, resource_id: str, focus: str = "general"
    ) -> AgentJob:
        """Create a scout AgentJob that explores the codebase for issues.

        The scout uses read-only tools and creates ManualTask entries
        from its findings.
        """
        resource = self.store.get_resource(resource_id)
        if not resource:
            raise ValueError(f"Resource {resource_id} not found")

        job = AgentJob(
            work_item_id=f"scout-{resource_id}",
            resource_id=resource_id,
            job_type=f"scout-{focus}",
        )
        self.store.save_agent_job(job)
        return job
