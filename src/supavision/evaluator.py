"""Report evaluator — assigns severity and decides whether to alert.

Two evaluation paths (Workstream A4):

1. **Structured path** — reads `Report.payload.status` / `.summary` / `.issues`
   when Claude submitted a valid `ReportPayload` via the `submit_report` tool.
   This is the primary path for opted-in resource types.
2. **Regex path (legacy fallback)** — pattern-matches over `report.content` to
   derive severity. Zero-cost, independent of Claude's cooperation.

On disagreement between the two paths, the more severe verdict wins and the
disagreement is logged for prompt-quality telemetry (R7 in the plan). This
preserves the regex safety net while letting the structured path drive UX.
"""

from __future__ import annotations

import logging
import re

from .models import EvalStrategy, Evaluation, Report, Severity
from .models.health import IssueSeverity, PayloadStatus, ReportPayload

logger = logging.getLogger(__name__)

# ── Severity ranking for disagreement resolution ────────────────────
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.HEALTHY: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}
_PAYLOAD_TO_SEVERITY: dict[PayloadStatus, Severity] = {
    PayloadStatus.HEALTHY: Severity.HEALTHY,
    PayloadStatus.WARNING: Severity.WARNING,
    PayloadStatus.CRITICAL: Severity.CRITICAL,
}

# ── Pattern tiers ───────────────────────────────────────────────

# Critical: something is actively broken or dangerous
_CRITICAL_PATTERNS = [
    re.compile(r"\b(critical|failure|down|outage|data.?loss|breach|compromised|emergency)\b", re.I),
    re.compile(r"\b(not running|service.+failed|connection refused)\b", re.I),
    re.compile(r"\b(disk|storage).{0,50}(100|9[5-9])\s*%", re.I),
    re.compile(r"\bstatus:\s*\*?\*?critical\*?\*?", re.I),
    re.compile(r"\b(cannot|unable to|failed to)\s+(connect|start|reach|resolve)\b", re.I),
    re.compile(r"\b(OOM|out of memory|killed by signal|segfault|core dump)\b", re.I),
    re.compile(r"\bCRITICAL\b"),  # Explicit status marker (case-sensitive)
    re.compile(r"\b(not responding|unreachable|connection.+timed?\s*out)\b", re.I),
    re.compile(r"\b(pool exhausted|no.+available|max.+connections?)\b", re.I),
    re.compile(r"\berror.+rate.{0,20}(100|[5-9]\d)\s*%", re.I),
    re.compile(r"\b(certificate|cert).{0,30}(expired|invalid|revoked)\b", re.I),
    re.compile(r"public.+(bucket|s3|storage).+accessible", re.I),
    re.compile(r"no.+encryption.+enabled", re.I),
    re.compile(r"replication.+lag.+(minutes|hours)", re.I),
    re.compile(r"ssh.+(fail|brute).{0,20}\d{3,}", re.I),
    re.compile(r"backup.+(missing|failed|not.+found|never)", re.I),
]

# Warning: something is degraded or at risk
_WARNING_PATTERNS = [
    re.compile(r"\b(warning|degraded|drift|anomaly|elevated|unusual|risk|concern)\b", re.I),
    re.compile(r"\b(high|excessive)\s+(cpu|memory|load|swap|usage)\b", re.I),
    re.compile(r"\b(disk|storage).{0,50}(8[0-9]|9[0-4])\s*%", re.I),
    re.compile(r"\b(restarted?|restart count).{0,20}\d+", re.I),
    re.compile(r"\b(slow|timeout|latency|backlog|queue)\b", re.I),
    re.compile(r"\bstatus:\s*\*?\*?warning\*?\*?", re.I),
    re.compile(r"\b(deprecated|expir|stale|outdated|end.of.life)\b", re.I),
    re.compile(r"\bWARNING\b"),  # Explicit status marker (case-sensitive)
    re.compile(r"\berror.+rate.{0,20}[1-4]\d?\s*%", re.I),
    re.compile(r"\b(certificate|cert|ssl).{0,30}(expir|renew).{0,30}\d+\s*days?\b", re.I),
    re.compile(r"\b(failed|failing)\s*:\s*\d+", re.I),
    re.compile(r"\b(swap|memory).{0,30}(7[0-9]|8[0-9])\s*%", re.I),
    re.compile(r"unattached.+(volume|ebs|disk)", re.I),
    re.compile(r"unused.+index", re.I),
    re.compile(r"table.+bloat.+(high|significant)", re.I),
    re.compile(r"idle.+(resource|instance|function)", re.I),
    re.compile(r"security.+group.+0\.0\.0\.0", re.I),
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
    """Return list of unique matched context lines (not just the keyword)."""
    matches = []
    seen = set()
    lines = content.split("\n")
    for pattern in patterns:
        for line in lines:
            if pattern.search(line):
                # Use the line as context (cleaned up, truncated)
                ctx = line.strip().lstrip("-*# ").strip()
                if ctx and ctx not in seen:
                    seen.add(ctx)
                    matches.append(ctx[:80])
                break  # One match per pattern is enough
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
        extra = f" (+{len(critical_matches) - 3} more)" if len(critical_matches) > 3 else ""
        summary = f"Critical issues detected: {details}{extra}"
    elif severity == Severity.WARNING:
        details = ", ".join(warning_matches[:3])
        extra = f" (+{len(warning_matches) - 3} more)" if len(warning_matches) > 3 else ""
        summary = f"Warning indicators: {details}{extra}"
    else:
        summary = "All checks passed — no issues detected"

    if prefix and severity != Severity.HEALTHY:
        return f"{prefix} {summary}"
    return summary


class Evaluator:
    """Dual-mode report evaluator: structured payload primary, regex fallback.

    Primary path reads `Report.payload` when present; fallback path is the
    original regex over `report.content`. On disagreement, the more severe
    verdict wins and the disagreement is logged (R7). No external API calls.
    """

    def __init__(self, **kwargs):
        # Accept but ignore api_key/model params for backward compatibility
        pass

    def evaluate(self, report: Report, strategy: EvalStrategy = EvalStrategy.KEYWORD) -> Evaluation:
        """Evaluate a report using structured payload + regex fallback.

        Strategy parameter is accepted for API compatibility. Structured mode
        activates automatically when `report.payload` exists and has a
        non-UNKNOWN status — no caller changes required.
        """
        regex_result = self._eval_rules(report)
        structured_result = self._eval_structured(report.payload) if report.payload else None

        if structured_result is None:
            severity, summary, should_alert = regex_result
            strategy_used = "keyword"
        else:
            severity, summary, should_alert, strategy_used = self._resolve_disagreement(
                structured_result, regex_result, report.id
            )

        return Evaluation(
            report_id=report.id,
            resource_id=report.resource_id,
            severity=severity,
            summary=summary,
            should_alert=should_alert,
            strategy_used=strategy_used,
        )

    def _eval_structured(
        self, payload: ReportPayload
    ) -> tuple[Severity, str, bool] | None:
        """Derive (severity, summary, should_alert) from a structured payload.

        Returns None if the payload is UNKNOWN — caller should fall back to
        the regex path (R1: Claude never called submit_report, engine wrote
        UNKNOWN as a placeholder).
        """
        if payload.status == PayloadStatus.UNKNOWN:
            return None

        severity = _PAYLOAD_TO_SEVERITY[payload.status]
        summary = payload.summary or "(no summary provided)"

        if severity == Severity.CRITICAL:
            should_alert = True
        elif severity == Severity.WARNING:
            # Alert on any warning payload that reports at least one issue.
            # A warning status with zero issues is unusual and we decline to
            # page a human on it.
            should_alert = any(
                i.severity in (IssueSeverity.WARNING, IssueSeverity.CRITICAL)
                for i in payload.issues
            )
        else:
            should_alert = False

        return severity, summary, should_alert

    @staticmethod
    def _resolve_disagreement(
        structured: tuple[Severity, str, bool],
        regex: tuple[Severity, str, bool],
        report_id: str,
    ) -> tuple[Severity, str, bool, str]:
        """Take the more severe of the two paths; log any disagreement.

        R7: the structured path is authoritative for UX (summary, issues),
        but the regex path is the zero-cost safety net. If regex says the
        report is more severe than structured did, we believe regex and log
        so a human can investigate the prompt-quality signal.
        """
        s_sev, s_summary, s_alert = structured
        r_sev, r_summary, r_alert = regex

        if s_sev == r_sev:
            # Agreement: structured wins on summary quality.
            return s_sev, s_summary, s_alert or r_alert, "structured"

        logger.warning(
            "Evaluator disagreement for report=%s: structured=%s regex=%s — taking more severe",
            report_id, s_sev, r_sev,
        )
        if _SEVERITY_RANK[s_sev] >= _SEVERITY_RANK[r_sev]:
            return s_sev, s_summary, s_alert or r_alert, "structured+keyword_disagreement"
        # Regex is more severe — keep regex severity, but prefer the
        # structured summary if it still makes sense. When regex escalates
        # past structured, use the regex summary (it describes what regex
        # saw that structured missed).
        return r_sev, r_summary, True, "keyword_override"

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
