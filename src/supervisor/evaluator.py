"""Report evaluator — assigns severity and decides whether to alert.

Rule-based evaluation: parses report structure and metrics to determine
severity without any external LLM calls. Zero additional cost.
"""

from __future__ import annotations

import logging
import re

from .models import EvalStrategy, Evaluation, Report, Severity

logger = logging.getLogger(__name__)

# ── Pattern tiers ───────────────────────────────────────────────

# Critical: something is actively broken or dangerous
_CRITICAL_PATTERNS = [
    re.compile(r"\b(critical|failure|down|outage|data.?loss|breach|compromised|emergency)\b", re.I),
    re.compile(r"\b(not running|service.+failed|connection refused|permission denied)\b", re.I),
    re.compile(r"\b(disk|storage).{0,30}(100|9[5-9])%", re.I),
    re.compile(r"\bstatus:\s*\*?\*?critical\*?\*?", re.I),
    re.compile(r"\b(cannot|unable to|failed to)\s+(connect|start|reach|resolve)\b", re.I),
    re.compile(r"\b(OOM|out of memory|killed by signal|segfault|core dump)\b", re.I),
    re.compile(r"\bCRITICAL\b"),  # Explicit status marker (case-sensitive)
]

# Warning: something is degraded or at risk
_WARNING_PATTERNS = [
    re.compile(r"\b(warning|degraded|drift|anomaly|elevated|unusual|risk|concern)\b", re.I),
    re.compile(r"\b(high|excessive)\s+(cpu|memory|load|swap|usage)\b", re.I),
    re.compile(r"\b(disk|storage).{0,30}(8[0-9]|9[0-4])%", re.I),
    re.compile(r"\b(restarted?|restart count).{0,20}\d{2,}", re.I),  # 10+ restarts
    re.compile(r"\b(slow|timeout|latency|backlog|queue)\b", re.I),
    re.compile(r"\bstatus:\s*\*?\*?warning\*?\*?", re.I),
    re.compile(r"\b(deprecated|expir|stale|outdated|end.of.life)\b", re.I),
    re.compile(r"\bWARNING\b"),  # Explicit status marker (case-sensitive)
]

# Healthy indicators (used to boost confidence in healthy verdict)
_HEALTHY_PATTERNS = [
    re.compile(r"\b(healthy|normal|stable|operational|running|active)\b", re.I),
    re.compile(r"\bstatus:\s*\*?\*?healthy\*?\*?", re.I),
    re.compile(r"\bno\s+(issues?|errors?|warnings?|problems?)\b", re.I),
    re.compile(r"\ball\s+(services?|checks?|systems?).{0,20}(pass|ok|running|healthy)\b", re.I),
]


def _extract_status_line(content: str) -> str | None:
    """Extract explicit status line from structured report (e.g., '## Status: **CRITICAL**')."""
    match = re.search(
        r"(?:^|\n)\s*(?:##?\s*)?(?:\*\*)?status(?:\*\*)?:\s*(?:\*\*)?(\w+)(?:\*\*)?",
        content, re.I,
    )
    return match.group(1).lower() if match else None


def _count_matches(content: str, patterns: list[re.Pattern]) -> list[str]:
    """Return list of unique matched pattern descriptions."""
    matches = []
    for pattern in patterns:
        found = pattern.search(content)
        if found:
            matches.append(found.group(0).strip())
    return matches


def _build_summary(severity: Severity, critical_matches: list[str],
                   warning_matches: list[str], explicit_status: str | None) -> str:
    """Build a concise, specific summary from matched patterns."""
    if explicit_status:
        prefix = f"Report status: {explicit_status}."
    else:
        prefix = ""

    if severity == Severity.CRITICAL:
        details = ", ".join(critical_matches[:3])
        summary = f"Critical issues detected: {details}"
    elif severity == Severity.WARNING:
        details = ", ".join(warning_matches[:3])
        summary = f"Warning indicators: {details}"
    else:
        summary = "All checks passed — no issues detected"

    if prefix and severity != Severity.HEALTHY:
        return f"{prefix} {summary}"
    return summary


class Evaluator:
    """Rule-based report evaluator. No external API calls — zero additional cost."""

    def __init__(self, **kwargs):
        # Accept but ignore api_key/model params for backward compatibility
        pass

    def evaluate(self, report: Report, strategy: EvalStrategy = EvalStrategy.KEYWORD) -> Evaluation:
        """Evaluate a report using rule-based analysis.

        Strategy parameter is accepted for API compatibility but all strategies
        now use the same rule-based approach (no LLM calls).
        """
        severity, summary, should_alert = self._eval_rules(report)

        return Evaluation(
            report_id=report.id,
            resource_id=report.resource_id,
            severity=severity,
            summary=summary,
            should_alert=should_alert,
            strategy_used=EvalStrategy.KEYWORD,
        )

    def _eval_rules(self, report: Report) -> tuple[Severity, str, bool]:
        """Smart rule-based evaluation.

        Decision hierarchy:
        1. Explicit status line in report (## Status: CRITICAL) takes priority
        2. Pattern matching on content with tiered severity
        3. Critical + Warning counts determine final severity
        """
        content = report.content or ""

        # 1. Check for explicit status line (most reliable)
        explicit_status = _extract_status_line(content)
        if explicit_status in ("critical", "crit"):
            critical_matches = _count_matches(content, _CRITICAL_PATTERNS) or ["explicit critical status"]
            return Severity.CRITICAL, _build_summary(Severity.CRITICAL, critical_matches, [], explicit_status), True
        if explicit_status in ("warning", "warn"):
            warning_matches = _count_matches(content, _WARNING_PATTERNS) or ["explicit warning status"]
            return Severity.WARNING, _build_summary(Severity.WARNING, [], warning_matches, explicit_status), True
        if explicit_status in ("healthy", "ok", "normal", "good"):
            return Severity.HEALTHY, _build_summary(Severity.HEALTHY, [], [], explicit_status), False

        # 2. Pattern matching
        critical_matches = _count_matches(content, _CRITICAL_PATTERNS)
        warning_matches = _count_matches(content, _WARNING_PATTERNS)
        healthy_matches = _count_matches(content, _HEALTHY_PATTERNS)

        # 3. Decision logic
        if critical_matches:
            return (
                Severity.CRITICAL,
                _build_summary(Severity.CRITICAL, critical_matches, warning_matches, None),
                True,  # Always alert on critical
            )

        if warning_matches:
            # Only alert if multiple warning signals or no healthy signals
            should_alert = len(warning_matches) >= 2 or not healthy_matches
            return (
                Severity.WARNING,
                _build_summary(Severity.WARNING, [], warning_matches, None),
                should_alert,
            )

        return Severity.HEALTHY, _build_summary(Severity.HEALTHY, [], [], None), False
