"""Report evaluator — assigns severity and decides whether to alert.

Three strategies:
  - LLM: Send the report to Claude with a short evaluation prompt.
  - KEYWORD: Pattern match on report text.
  - HYBRID: Keyword first, LLM for ambiguous cases.
"""

from __future__ import annotations

import json
import logging
import re

import anthropic

from .models import EvalStrategy, Evaluation, Report, Severity

logger = logging.getLogger(__name__)

# Keywords that indicate severity levels
_CRITICAL_PATTERNS = re.compile(
    r"\b(critical|failure|down|outage|data loss|breach|compromised|emergency)\b",
    re.IGNORECASE,
)
_WARNING_PATTERNS = re.compile(
    r"\b(warning|degraded|drift|anomaly|elevated|unusual|risk|concern)\b",
    re.IGNORECASE,
)

_EVAL_SYSTEM_PROMPT = """\
You are evaluating an infrastructure monitoring report. Assign a severity level.

Rules:
- "critical": something is broken, actively losing data, or a security breach is in progress
- "warning": something is degraded, drifted from baseline, or at risk of failure
- "healthy": everything is operating as expected

- should_alert is true for critical always
- should_alert is true for warning only if the issue is new or worsening
- should_alert is false for healthy

Respond with JSON only:
{"severity": "healthy" | "warning" | "critical", "summary": "one sentence", "should_alert": true | false}
"""


class Evaluator:
    """Evaluates reports to assign severity and decide on alerts."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = "claude-sonnet-4-20250514"):
        self._client = client
        self._model = model

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def evaluate(self, report: Report, strategy: EvalStrategy) -> Evaluation:
        """Evaluate a report using the specified strategy."""
        if strategy == EvalStrategy.KEYWORD:
            severity, summary, should_alert = self._eval_keyword(report)
        elif strategy == EvalStrategy.LLM:
            severity, summary, should_alert = self._eval_llm(report)
        elif strategy == EvalStrategy.HYBRID:
            severity, summary, should_alert = self._eval_hybrid(report)
        else:
            severity, summary, should_alert = self._eval_keyword(report)

        return Evaluation(
            report_id=report.id,
            resource_id=report.resource_id,
            severity=severity,
            summary=summary,
            should_alert=should_alert,
            strategy_used=strategy,
        )

    def _eval_keyword(self, report: Report) -> tuple[Severity, str, bool]:
        """Simple keyword matching on report text."""
        content = report.content

        if _CRITICAL_PATTERNS.search(content):
            return Severity.CRITICAL, "Critical keywords detected in report", True

        if _WARNING_PATTERNS.search(content):
            return Severity.WARNING, "Warning indicators found in report", False

        return Severity.HEALTHY, "No concerning patterns detected", False

    def _eval_llm(self, report: Report) -> tuple[Severity, str, bool]:
        """Ask Claude to evaluate the report severity."""
        try:
            client = self._get_client()
            response = client.messages.create(
                model=self._model,
                max_tokens=256,
                system=_EVAL_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": f"Evaluate this report:\n\n{report.content[:4000]}"}
                ],
            )

            raw = response.content[0].text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            data = json.loads(raw)
            severity = Severity(data.get("severity", "healthy"))
            summary = data.get("summary", "")
            should_alert = data.get("should_alert", False)

            return severity, summary, should_alert

        except Exception as e:
            logger.warning("LLM evaluation failed, falling back to keyword: %s", e)
            return self._eval_keyword(report)

    def _eval_hybrid(self, report: Report) -> tuple[Severity, str, bool]:
        """Keyword first. If healthy, accept. Otherwise confirm with LLM."""
        severity, summary, should_alert = self._eval_keyword(report)

        if severity == Severity.HEALTHY:
            return severity, summary, should_alert

        # Keyword found something — confirm with LLM
        return self._eval_llm(report)
