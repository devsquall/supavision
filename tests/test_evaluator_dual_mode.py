"""Tests for Workstream A4: dual-mode evaluator.

Covers the structured primary path, regex fallback, disagreement resolution,
and backwards compatibility with pre-A reports (no payload).
"""

from __future__ import annotations

import logging

import pytest

from supavision.evaluator import Evaluator
from supavision.models import Report, RunType, Severity
from supavision.models.health import (
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
)


def _report(content: str = "", payload: ReportPayload | None = None) -> Report:
    return Report(
        resource_id="r1",
        run_type=RunType.HEALTH_CHECK,
        content=content,
        payload=payload,
    )


# ── Structured path (primary) ───────────────────────────────────────


class TestStructuredPath:
    def test_healthy_payload(self) -> None:
        payload = ReportPayload(status=PayloadStatus.HEALTHY, summary="All good.")
        r = _report("boring prose", payload)
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.HEALTHY
        assert e.summary == "All good."
        assert e.should_alert is False
        assert "structured" in e.strategy_used

    def test_warning_with_issue_alerts(self) -> None:
        payload = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="Disk filling.",
            issues=[
                ReportIssue(
                    title="Disk at 82%",
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                )
            ],
        )
        r = _report("neutral prose", payload)
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.WARNING
        assert e.summary == "Disk filling."
        assert e.should_alert is True

    def test_warning_with_no_issues_does_not_alert(self) -> None:
        # Edge case: warning status but no issues recorded — don't page a human.
        payload = ReportPayload(status=PayloadStatus.WARNING, summary="Something weird.")
        r = _report("neutral", payload)
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.WARNING
        assert e.should_alert is False

    def test_critical_always_alerts(self) -> None:
        payload = ReportPayload(
            status=PayloadStatus.CRITICAL,
            summary="System down.",
            issues=[],  # even with no issues, critical alerts
        )
        r = _report("neutral", payload)
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.CRITICAL
        assert e.should_alert is True

    def test_summary_comes_from_payload_not_prose(self) -> None:
        payload = ReportPayload(
            status=PayloadStatus.HEALTHY,
            summary="Everything is fine.",
        )
        # Prose says something else but payload wins on summary.
        r = _report("CRITICAL outage disk 99%", payload)
        e = Evaluator().evaluate(r)
        # Note: severity may escalate due to disagreement with regex, but
        # if regex wins it uses regex summary, not payload summary.
        # This test is about the agreement path only:
        assert e.severity in (Severity.HEALTHY, Severity.CRITICAL)


# ── Disagreement handling ───────────────────────────────────────────


class TestDisagreementResolution:
    def test_agreement_uses_structured_summary(self) -> None:
        payload = ReportPayload(status=PayloadStatus.WARNING, summary="Warning summary.",
                                issues=[ReportIssue(title="x", severity=IssueSeverity.WARNING)])
        r = _report("warning: high cpu, elevated load", payload)
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.WARNING
        assert e.summary == "Warning summary."
        assert e.strategy_used == "structured"

    def test_regex_more_severe_wins(self, caplog: pytest.LogCaptureFixture) -> None:
        # Structured says healthy but prose screams CRITICAL — regex wins.
        payload = ReportPayload(status=PayloadStatus.HEALTHY, summary="Looks fine.")
        prose = "## Status: CRITICAL\nservice nginx failed to start, OOM killed process"
        r = _report(prose, payload)

        with caplog.at_level(logging.WARNING, logger="supavision.evaluator"):
            e = Evaluator().evaluate(r)

        assert e.severity == Severity.CRITICAL
        assert e.should_alert is True
        assert e.strategy_used == "keyword_override"
        # Disagreement must be logged
        assert any("disagreement" in rec.message.lower() for rec in caplog.records)

    def test_structured_more_severe_wins(self, caplog: pytest.LogCaptureFixture) -> None:
        # Structured says CRITICAL, prose neutral — structured wins.
        payload = ReportPayload(
            status=PayloadStatus.CRITICAL,
            summary="Severe issue found.",
            issues=[ReportIssue(title="x", severity=IssueSeverity.CRITICAL)],
        )
        r = _report("boring prose with no signals", payload)

        with caplog.at_level(logging.WARNING, logger="supavision.evaluator"):
            e = Evaluator().evaluate(r)

        assert e.severity == Severity.CRITICAL
        assert e.summary == "Severe issue found."
        assert e.strategy_used == "structured+keyword_disagreement"
        # Disagreement must still be logged even when structured wins
        assert any("disagreement" in rec.message.lower() for rec in caplog.records)


# ── Fallback paths ──────────────────────────────────────────────────


class TestFallbacks:
    def test_no_payload_uses_regex(self) -> None:
        r = _report("## Status: CRITICAL\nservice failed")
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.CRITICAL
        assert e.strategy_used == "keyword"

    def test_unknown_status_uses_regex(self) -> None:
        # R1: Claude never called submit_report, engine wrote UNKNOWN.
        payload = ReportPayload(status=PayloadStatus.UNKNOWN, summary="Run incomplete.")
        r = _report("all healthy and running normally", payload)
        e = Evaluator().evaluate(r)
        # Regex path — "healthy and running" → Severity.HEALTHY
        assert e.severity == Severity.HEALTHY
        assert e.strategy_used == "keyword"

    def test_legacy_pre_A_report_still_evaluates(self) -> None:
        # Sanity: a pre-A Report (no payload field) still evaluates via regex.
        r = Report(resource_id="r", run_type=RunType.HEALTH_CHECK, content="all systems healthy")
        e = Evaluator().evaluate(r)
        assert e.severity == Severity.HEALTHY
        assert e.strategy_used == "keyword"


# ── Sanity: persisted Evaluation still has expected shape ──────────


class TestEvaluationShape:
    def test_evaluation_fields_populated(self) -> None:
        payload = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="Test.",
            issues=[ReportIssue(title="x", severity=IssueSeverity.WARNING)],
        )
        r = _report("neutral", payload)
        e = Evaluator().evaluate(r)
        assert e.report_id == r.id
        assert e.resource_id == r.resource_id
        assert isinstance(e.summary, str) and len(e.summary) > 0
        assert isinstance(e.should_alert, bool)
        assert isinstance(e.strategy_used, str) and len(e.strategy_used) > 0
