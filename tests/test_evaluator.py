"""Tests for the rule-based evaluator."""

from __future__ import annotations

from supavision.evaluator import (
    _CRITICAL_PATTERNS,
    _HEALTHY_PATTERNS,
    _WARNING_PATTERNS,
    Evaluator,
    _build_summary,
    _count_matches,
    _extract_status_line,
)
from supavision.models import EvalStrategy, Report, RunType, Severity


def _report(content: str) -> Report:
    return Report(resource_id="r1", run_type=RunType.HEALTH_CHECK, content=content)


# ── Status line extraction ──────────────────────────────────────


class TestStatusLine:
    def test_markdown_status(self):
        assert _extract_status_line("## Status: **CRITICAL**") == "critical"

    def test_plain_status(self):
        assert _extract_status_line("Status: warning") == "warning"

    def test_healthy_status(self):
        assert _extract_status_line("## Status: **healthy**") == "healthy"

    def test_no_status(self):
        assert _extract_status_line("Some report without status") is None

    def test_status_in_body(self):
        content = "Overview\n\n## Status: CRITICAL\n\nDetails here"
        assert _extract_status_line(content) == "critical"


# ── Pattern matching ────────────────────────────────────────────


class TestPatterns:
    def test_critical_keywords(self):
        assert _count_matches("Server is down, critical failure", _CRITICAL_PATTERNS)

    def test_critical_disk_95_plus(self):
        assert _count_matches("Disk usage at 97%", _CRITICAL_PATTERNS)

    def test_critical_service_failed(self):
        assert _count_matches("nginx service failed to start", _CRITICAL_PATTERNS)

    def test_critical_oom(self):
        assert _count_matches("Process killed by OOM killer", _CRITICAL_PATTERNS)

    def test_warning_keywords(self):
        assert _count_matches("Performance degraded, high CPU usage", _WARNING_PATTERNS)

    def test_warning_disk_80_94(self):
        assert _count_matches("Disk usage at 85%", _WARNING_PATTERNS)

    def test_warning_restarts(self):
        assert _count_matches("restart count: 31", _WARNING_PATTERNS)

    def test_healthy_keywords(self):
        assert _count_matches("All services running normally", _HEALTHY_PATTERNS)

    def test_no_critical_on_normal_text(self):
        assert not _count_matches("Everything is fine, all services healthy", _CRITICAL_PATTERNS)


# ── Full evaluation ─────────────────────────────────────────────


class TestEvaluator:
    def setup_method(self):
        self.evaluator = Evaluator()

    def test_critical_report(self):
        report = _report("## Status: **CRITICAL**\n\nServer is down. Connection refused on port 80.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.CRITICAL
        assert evaluation.should_alert is True
        assert evaluation.summary

    def test_warning_report(self):
        report = _report("## Status: **WARNING**\n\nDisk usage elevated at 87%.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.WARNING
        assert evaluation.should_alert is True

    def test_healthy_report(self):
        report = _report("## Status: **healthy**\n\nAll services running. No issues detected.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.HEALTHY
        assert evaluation.should_alert is False

    def test_critical_without_status_line(self):
        report = _report("The database is down. Connection refused. Data loss possible.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.CRITICAL
        assert evaluation.should_alert is True

    def test_warning_without_status_line(self):
        report = _report("CPU usage is elevated and there's a risk of timeout.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.WARNING

    def test_healthy_without_status_line(self):
        report = _report("All systems operational. Memory at 45%. Disk at 60%.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.HEALTHY

    def test_critical_overrides_warning(self):
        report = _report("Warning: disk high. CRITICAL: service down and data loss detected.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.CRITICAL

    def test_strategy_param_accepted(self):
        """All strategies use rule-based now, but param should be accepted."""
        report = _report("## Status: **healthy**\nAll good.")
        for strategy in [EvalStrategy.KEYWORD, EvalStrategy.LLM, EvalStrategy.HYBRID]:
            evaluation = self.evaluator.evaluate(report, strategy)
            assert evaluation.severity == Severity.HEALTHY

    def test_empty_report(self):
        report = _report("")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.HEALTHY

    def test_summary_includes_details(self):
        report = _report("nginx is not running. Connection refused on port 443.")
        evaluation = self.evaluator.evaluate(report)
        assert "not running" in evaluation.summary.lower() or "connection refused" in evaluation.summary.lower()

    def test_single_warning_with_many_healthy_signals_no_alert(self):
        """Single warning + multiple healthy signals = warning but no alert."""
        content = "All services running healthy and operational. No errors found. System is stable. Minor concern."
        report = _report(content)
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.WARNING
        assert evaluation.should_alert is False

    def test_multiple_warnings_always_alert(self):
        """Multiple warning signals = always alert."""
        report = _report("Elevated CPU. High memory usage. Degraded performance. Risk of timeout.")
        evaluation = self.evaluator.evaluate(report)
        assert evaluation.severity == Severity.WARNING
        assert evaluation.should_alert is True


# ── Summary building ────────────────────────────────────────────


class TestSummary:
    def test_critical_summary(self):
        s = _build_summary(Severity.CRITICAL, ["down", "failure"], [], None)
        assert "Critical" in s
        assert "down" in s

    def test_warning_summary(self):
        s = _build_summary(Severity.WARNING, [], ["high CPU", "degraded"], None)
        assert "Warning" in s
        assert "high CPU" in s

    def test_healthy_summary(self):
        s = _build_summary(Severity.HEALTHY, [], [], None)
        assert "passed" in s.lower() or "no issues" in s.lower()

    def test_explicit_status_prefix(self):
        s = _build_summary(Severity.CRITICAL, ["down"], [], "critical")
        assert "critical" in s.lower()

    def test_max_3_details(self):
        s = _build_summary(Severity.CRITICAL, ["alpha", "bravo", "charlie", "delta", "echo"], [], None)
        assert "alpha" in s
        assert "bravo" in s
        assert "charlie" in s
        assert "delta" not in s
        assert "echo" not in s
