"""Tests for Workstream A7: Slack alert payload cites top issue + diff + deep link.

Verifies:
- Slack Block Kit payload includes top issue title + recommendation when structured
- Diff counts (+new, -resolved, =persisted) appear when payload_diff is set
- Deep link to /reports/{id} is rendered when SUPAVISION_BASE_URL is set
- Legacy prose-only reports still work
- Dedup key uses stable top-issue id for structured reports (R12)
"""

from __future__ import annotations

import pytest

from supavision.models import Evaluation, Report, Resource, RunType, Severity
from supavision.models.health import (
    IssueDiff,
    IssueDiffEntry,
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
)
from supavision.notifications import SlackChannel, _dedup_key, _select_top_issue

# ── _select_top_issue ───────────────────────────────────────────────


class TestSelectTopIssue:
    def test_empty_returns_none(self) -> None:
        p = ReportPayload(status=PayloadStatus.HEALTHY, summary="ok")
        assert _select_top_issue(p) is None

    def test_critical_beats_warning(self) -> None:
        p = ReportPayload(
            status=PayloadStatus.CRITICAL,
            summary="x",
            issues=[
                ReportIssue(title="A warn", severity=IssueSeverity.WARNING, tags=["disk"], scope="/var"),
                ReportIssue(title="B crit", severity=IssueSeverity.CRITICAL, tags=["memory"], scope="h"),
                ReportIssue(title="C info", severity=IssueSeverity.INFO),
            ],
        )
        top = _select_top_issue(p)
        assert top is not None
        assert top.title == "B crit"

    def test_ties_prefer_first(self) -> None:
        p = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="x",
            issues=[
                ReportIssue(title="First", severity=IssueSeverity.WARNING, tags=["disk"], scope="/var"),
                ReportIssue(title="Second", severity=IssueSeverity.WARNING, tags=["memory"], scope="h"),
            ],
        )
        top = _select_top_issue(p)
        assert top is not None
        assert top.title == "First"


# ── SlackChannel payload ─────────────────────────────────────────────


def _resource() -> Resource:
    return Resource(name="prod-01", resource_type="server")


def _eval(severity: Severity = Severity.WARNING, summary: str = "Disk at 82%") -> Evaluation:
    return Evaluation(
        report_id="r1",
        resource_id="res1",
        severity=severity,
        summary=summary,
        should_alert=True,
    )


def _structured_report(
    *,
    issues: list[ReportIssue] | None = None,
    diff: IssueDiff | None = None,
) -> Report:
    payload = ReportPayload(
        status=PayloadStatus.WARNING,
        summary="test",
        issues=issues or [],
    )
    return Report(
        resource_id="res1",
        run_type=RunType.HEALTH_CHECK,
        content="raw prose",
        payload=payload,
        payload_diff=diff,
    )


def _block_texts(payload: dict) -> list[str]:
    """Flatten all text content from a Slack Block Kit payload for assertions."""
    texts = []
    for att in payload.get("attachments", []):
        for block in att.get("blocks", []):
            t = block.get("text")
            if isinstance(t, dict) and "text" in t:
                texts.append(t["text"])
            for elem in block.get("elements", []):
                if isinstance(elem, dict) and "text" in elem:
                    texts.append(elem["text"])
    return texts


class TestStructuredSlackPayload:
    def _channel(self) -> SlackChannel:
        return SlackChannel("https://hooks.slack.com/services/T/B/xxxxxx")

    def test_top_issue_rendered(self) -> None:
        issue = ReportIssue(
            title="Disk will be full in 28 days",
            severity=IssueSeverity.WARNING,
            recommendation="Rotate /var/log/app/*.log",
            tags=["disk"],
            scope="/var",
        )
        report = _structured_report(issues=[issue])
        payload = self._channel()._build_payload(_resource(), report, _eval())
        blob = "\n".join(_block_texts(payload))
        assert "Disk will be full in 28 days" in blob
        assert "Rotate /var/log/app/*.log" in blob
        assert "/var" in blob  # scope

    def test_critical_issue_chosen_over_warning(self) -> None:
        report = _structured_report(
            issues=[
                ReportIssue(title="Noise", severity=IssueSeverity.WARNING, tags=["disk"], scope="/var"),
                ReportIssue(
                    title="FIRE",
                    severity=IssueSeverity.CRITICAL,
                    recommendation="Wake ops",
                    tags=["service"],
                    scope="db",
                ),
            ]
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        assert "FIRE" in blob
        assert "Wake ops" in blob

    def test_diff_counts_rendered(self) -> None:
        diff = IssueDiff(
            new=[IssueDiffEntry(id="memory-host", title="mem", severity=IssueSeverity.WARNING)],
            resolved=[
                IssueDiffEntry(id="a-b", title="old", severity=IssueSeverity.WARNING),
                IssueDiffEntry(id="c-d", title="other", severity=IssueSeverity.INFO),
            ],
            persisted=[IssueDiffEntry(id="disk-var", title="disk", severity=IssueSeverity.WARNING)],
        )
        report = _structured_report(
            issues=[
                ReportIssue(title="Issue", severity=IssueSeverity.WARNING, tags=["memory"], scope="host"),
            ],
            diff=diff,
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        assert "+1 new" in blob
        assert "2 resolved" in blob
        assert "1 persisted" in blob

    def test_no_diff_section_when_empty_diff(self) -> None:
        # A diff with all zeros shouldn't add noise.
        diff = IssueDiff(new=[], resolved=[], persisted=[])
        report = _structured_report(
            issues=[ReportIssue(title="x", severity=IssueSeverity.WARNING)],
            diff=diff,
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        assert "Since last run" not in blob

    def test_deep_link_when_base_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPAVISION_BASE_URL", "https://supavision.example/")
        report = _structured_report(
            issues=[ReportIssue(title="x", severity=IssueSeverity.WARNING)]
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        assert "https://supavision.example/reports/" in blob
        assert "View report" in blob

    def test_no_deep_link_without_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPAVISION_BASE_URL", raising=False)
        report = _structured_report(
            issues=[ReportIssue(title="x", severity=IssueSeverity.WARNING)]
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        assert "View report" not in blob
        assert "Report: `" in blob  # legacy-style footer

    def test_legacy_report_uses_prose_preview(self) -> None:
        report = Report(
            resource_id="res1",
            run_type=RunType.HEALTH_CHECK,
            content="legacy prose body about disk",
        )
        blob = "\n".join(
            _block_texts(self._channel()._build_payload(_resource(), report, _eval()))
        )
        # Legacy format wraps prose in a code block
        assert "legacy prose body about disk" in blob
        # And has no "Top issue:" label
        assert "Top issue:" not in blob


# ── Dedup key stability (R12) ───────────────────────────────────────


class TestDedupKey:
    def test_structured_dedup_uses_top_issue_id(self) -> None:
        res = _resource()
        ev = _eval()
        # Same top issue id (disk-var), different summaries → same key
        r1 = _structured_report(
            issues=[
                ReportIssue(
                    title="Disk almost full",
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                )
            ]
        )
        r2 = _structured_report(
            issues=[
                ReportIssue(
                    title="Disk filling up",  # reworded
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                )
            ]
        )
        assert _dedup_key(res, ev, r1) == _dedup_key(res, ev, r2)

    def test_structured_dedup_different_issues_distinct(self) -> None:
        res = _resource()
        ev = _eval()
        r1 = _structured_report(
            issues=[ReportIssue(title="disk", severity=IssueSeverity.WARNING, tags=["disk"], scope="/var")]
        )
        r2 = _structured_report(
            issues=[ReportIssue(title="memory", severity=IssueSeverity.WARNING, tags=["memory"], scope="host")]
        )
        assert _dedup_key(res, ev, r1) != _dedup_key(res, ev, r2)

    def test_legacy_dedup_backward_compatible(self) -> None:
        res = _resource()
        ev = _eval(summary="Identical summary")
        r = Report(resource_id="res1", run_type=RunType.HEALTH_CHECK, content="x")
        # Call without report (old call sites) and with report (new call sites)
        # must produce the same legacy-compatible key for the same summary.
        k_old = _dedup_key(res, ev)
        k_new = _dedup_key(res, ev, r)
        assert k_old == k_new
