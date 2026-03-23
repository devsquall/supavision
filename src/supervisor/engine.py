"""Execution engine — the core of Supervisor.

Orchestrates: template → Claude API → report → evaluation.
Handles both discovery (Phase 1) and health check (Phase 2) flows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import anthropic

from .db import Store
from .evaluator import Evaluator
from .models import (
    Checklist,
    ChecklistItem,
    Evaluation,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
    SystemContext,
)
from .templates import (
    TEMPLATE_DIR_DEFAULT,
    load_template,
    resolve_credentials,
    resolve_template,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"


class Engine:
    """Execution engine for discovery and health check runs."""

    def __init__(
        self,
        store: Store,
        template_dir: str = TEMPLATE_DIR_DEFAULT,
        model: str = DEFAULT_MODEL,
        client: anthropic.Anthropic | None = None,
    ):
        self.store = store
        self.template_dir = template_dir
        self.model = model
        self._client = client
        self._evaluator = Evaluator(client=client, model=model)

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    # ── Public API ───────────────────────────────────────────────────

    def run_discovery(self, resource_id: str) -> Run:
        """Execute Phase 1: Discovery.

        1. Load resource + walk parent chain for inherited credentials
        2. Load discovery template
        3. Resolve placeholders
        4. Call Claude API
        5. Parse response → extract system context and checklist
        6. Compare with previous context for drift (if re-discovery)
        7. Store results + run evaluation
        """
        resource = self.store.get_resource(resource_id)
        if not resource:
            raise ValueError(f"Resource {resource_id} not found")

        run = Run(resource_id=resource_id, run_type=RunType.DISCOVERY, status=RunStatus.RUNNING)
        run.started_at = datetime.now(timezone.utc)
        self.store.save_run(run)

        try:
            # Resolve credentials from parent chain
            creds = self._resolve_full_credentials(resource)

            # Load and resolve template
            template = load_template(
                resource.resource_type, RunType.DISCOVERY, self.template_dir
            )

            # Include previous context for drift detection
            prev_context = self.store.get_latest_context(resource_id)
            runtime_ctx = {}
            if prev_context:
                runtime_ctx["previous_context"] = prev_context.content

            # Include monitoring requests
            if resource.monitoring_requests:
                runtime_ctx["monitoring_requests"] = "\n".join(
                    f"- {req}" for req in resource.monitoring_requests
                )

            resolved = resolve_template(template, resource, creds, runtime_ctx)

            # Call Claude
            response = self._call_claude(
                system_prompt=resolved,
                user_message=f"Begin discovery for resource: {resource.name}",
            )

            # Parse sections from response
            system_context_content, checklist_items = self._parse_discovery_response(response)

            # Store system context (versioned)
            version = (prev_context.version + 1) if prev_context else 1
            ctx = SystemContext(
                resource_id=resource_id,
                content=system_context_content or response,
                version=version,
            )
            self.store.save_context(ctx)

            # Store checklist (versioned, preserve team requests)
            prev_checklist = self.store.get_latest_checklist(resource_id)
            team_items = []
            if prev_checklist:
                team_items = [i for i in prev_checklist.items if i.source == "team_request"]

            all_items = checklist_items + team_items
            checklist = Checklist(
                resource_id=resource_id,
                items=all_items,
                version=version,
            )
            self.store.save_checklist(checklist)

            # Store report
            report = Report(
                resource_id=resource_id,
                run_type=RunType.DISCOVERY,
                content=response,
            )
            self.store.save_report(report)

            # Evaluate
            evaluation = self._evaluator.evaluate(report, resource.eval_strategy)
            self.store.save_evaluation(evaluation)

            # Update run
            run.report_id = report.id
            run.evaluation_id = evaluation.id
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)

            logger.info(
                "Discovery completed: resource=%s severity=%s",
                resource.name, evaluation.severity,
            )

            return run

        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)
            logger.error("Discovery failed: resource=%s error=%s", resource_id, e)
            raise

    def run_health_check(self, resource_id: str) -> Run:
        """Execute Phase 2: Health Check.

        1. Load resource + inherited credentials
        2. Load latest SystemContext and Checklist
        3. Load recent reports for trend context
        4. Resolve health check template
        5. Call Claude API
        6. Store Report + Evaluation
        """
        resource = self.store.get_resource(resource_id)
        if not resource:
            raise ValueError(f"Resource {resource_id} not found")

        run = Run(resource_id=resource_id, run_type=RunType.HEALTH_CHECK, status=RunStatus.RUNNING)
        run.started_at = datetime.now(timezone.utc)
        self.store.save_run(run)

        try:
            creds = self._resolve_full_credentials(resource)

            # Load context from discovery
            latest_ctx = self.store.get_latest_context(resource_id)
            latest_checklist = self.store.get_latest_checklist(resource_id)
            recent_reports = self.store.get_recent_reports(
                resource_id, RunType.HEALTH_CHECK, limit=3
            )

            # Build runtime context
            runtime_ctx: dict[str, str] = {}
            if latest_ctx:
                runtime_ctx["system_context"] = latest_ctx.content
            if latest_checklist:
                runtime_ctx["checklist"] = "\n".join(
                    f"- [ ] {item.description}" for item in latest_checklist.items
                )
            if recent_reports:
                summaries = []
                for i, rpt in enumerate(recent_reports):
                    summaries.append(f"### Report {i + 1} ({rpt.created_at})\n{rpt.content[:1000]}")
                runtime_ctx["recent_reports"] = "\n\n".join(summaries)

            if resource.monitoring_requests:
                runtime_ctx["monitoring_requests"] = "\n".join(
                    f"- {req}" for req in resource.monitoring_requests
                )

            # Load and resolve template
            template = load_template(
                resource.resource_type, RunType.HEALTH_CHECK, self.template_dir
            )
            resolved = resolve_template(template, resource, creds, runtime_ctx)

            # Call Claude
            response = self._call_claude(
                system_prompt=resolved,
                user_message=f"Run health check for resource: {resource.name}",
            )

            # Store report
            report = Report(
                resource_id=resource_id,
                run_type=RunType.HEALTH_CHECK,
                content=response,
            )
            self.store.save_report(report)

            # Evaluate
            evaluation = self._evaluator.evaluate(report, resource.eval_strategy)
            self.store.save_evaluation(evaluation)

            # Alert decision
            if evaluation.should_alert:
                self._handle_alert(resource, report, evaluation)

            # Update run
            run.report_id = report.id
            run.evaluation_id = evaluation.id
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)

            logger.info(
                "Health check completed: resource=%s severity=%s alert=%s",
                resource.name, evaluation.severity, evaluation.should_alert,
            )

            return run

        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)
            logger.error("Health check failed: resource=%s error=%s", resource_id, e)
            raise

    # ── Internal helpers ─────────────────────────────────────────────

    def _resolve_full_credentials(self, resource: Resource) -> dict[str, str]:
        """Walk parent chain, merge credentials, resolve env vars."""
        tree = self.store.get_resource_tree(resource.id)

        # Merge credentials from root to leaf (child overrides parent)
        merged_creds = {}
        for ancestor in tree:
            merged_creds.update(ancestor.credentials)

        return resolve_credentials(resource, merged_creds)

    def _call_claude(self, system_prompt: str, user_message: str) -> str:
        """Call the Anthropic Messages API."""
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def _parse_discovery_response(
        self, response: str
    ) -> tuple[str, list[ChecklistItem]]:
        """Parse discovery response into system context and checklist items.

        The discovery template instructs Claude to output sections delimited by:
          === SYSTEM CONTEXT ===
          ...
          === CHECKLIST ===
          - Item 1
          - Item 2
        """
        system_context = ""
        checklist_items: list[ChecklistItem] = []

        # Extract system context section
        ctx_match = response.split("=== SYSTEM CONTEXT ===")
        if len(ctx_match) > 1:
            after_ctx = ctx_match[1]
            # Everything until next === section or end
            if "=== CHECKLIST ===" in after_ctx:
                system_context = after_ctx.split("=== CHECKLIST ===")[0].strip()
            else:
                system_context = after_ctx.strip()

        # Extract checklist items
        checklist_match = response.split("=== CHECKLIST ===")
        if len(checklist_match) > 1:
            checklist_text = checklist_match[1].strip()
            # Parse bullet items
            for line in checklist_text.split("\n"):
                line = line.strip()
                if line.startswith("- "):
                    item_text = line[2:].strip()
                    # Remove checkbox markers if present
                    item_text = item_text.removeprefix("[ ] ").removeprefix("[x] ")
                    if item_text:
                        checklist_items.append(
                            ChecklistItem(description=item_text, source="discovery")
                        )

        return system_context, checklist_items

    def _handle_alert(
        self, resource: Resource, report: Report, evaluation: Evaluation
    ) -> None:
        """Handle an alert. v1: print to stdout. Future: Slack, webhook, etc."""
        print(f"\n{'='*60}")
        print(f"ALERT: {evaluation.severity.upper()} — {resource.name}")
        print(f"{'='*60}")
        print(f"Summary: {evaluation.summary}")
        print(f"Resource: {resource.name} ({resource.resource_type})")
        print(f"Report ID: {report.id}")
        print(f"{'='*60}\n")
