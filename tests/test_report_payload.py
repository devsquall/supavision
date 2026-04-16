"""Unit tests for Workstream A1: ReportPayload and related models.

Covers the structured report payload Claude will submit via the `submit_report`
tool. The payload shape is defined in models/health.py; these tests pin the
contract so downstream slices (A2 engine wiring, A3 persistence, A6 diff) can
depend on stable semantics.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from supavision.models.health import (
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
    RunMetadata,
    compute_issue_id,
)

# ── compute_issue_id ─────────────────────────────────────────────────


class TestComputeIssueId:
    def test_primary_tag_plus_scope(self) -> None:
        assert compute_issue_id(["disk"], "/var", "whatever") == "disk-var"

    def test_primary_tag_only(self) -> None:
        assert compute_issue_id(["brute-force"], None, "whatever") == "brute-force"

    def test_fallback_to_title_when_no_tags(self) -> None:
        assert compute_issue_id([], None, "SSH brute force attempts") == "ssh-brute-force-attempts"

    def test_lowercases_and_strips(self) -> None:
        assert compute_issue_id(["Disk"], "/VAR/LOG", "x") == "disk-var-log"

    def test_collapses_nonalnum_runs(self) -> None:
        assert compute_issue_id(["cert_expiry"], "app.example.com:443", "x") == "cert-expiry-app-example-com-443"

    def test_stability_across_calls(self) -> None:
        a = compute_issue_id(["disk"], "/var", "Disk filling up quickly")
        b = compute_issue_id(["disk"], "/var", "Disk almost full!")  # title drift
        assert a == b, "title changes must not affect the id"

    def test_scope_difference_produces_distinct_ids(self) -> None:
        a = compute_issue_id(["disk"], "/var", "x")
        b = compute_issue_id(["disk"], "/home", "x")
        assert a != b, "different scopes must produce different ids"

    def test_uses_primary_tag_only(self) -> None:
        # Second/third tags are metadata, not part of the id.
        a = compute_issue_id(["disk", "capacity"], "/var", "x")
        b = compute_issue_id(["disk", "security"], "/var", "x")
        assert a == b

    def test_empty_everything_returns_unknown(self) -> None:
        assert compute_issue_id([], None, "") == "unknown"

    def test_max_length_80(self) -> None:
        long_title = "a" * 500
        result = compute_issue_id([], None, long_title)
        assert len(result) <= 80


# ── ReportIssue ──────────────────────────────────────────────────────


class TestReportIssue:
    def test_minimal_valid(self) -> None:
        issue = ReportIssue(title="Disk full", severity=IssueSeverity.CRITICAL)
        assert issue.title == "Disk full"
        assert issue.severity == IssueSeverity.CRITICAL
        assert issue.id  # auto-derived
        assert issue.evidence == ""
        assert issue.recommendation == ""
        assert issue.tags == []
        assert issue.scope is None

    def test_id_derived_from_tags_and_scope(self) -> None:
        issue = ReportIssue(
            title="Disk filling up",
            severity=IssueSeverity.WARNING,
            tags=["disk"],
            scope="/var",
        )
        assert issue.id == "disk-var"

    def test_id_derived_from_title_when_no_tags(self) -> None:
        issue = ReportIssue(title="Cert expiring soon", severity=IssueSeverity.WARNING)
        assert issue.id == "cert-expiring-soon"

    def test_explicit_id_preserved(self) -> None:
        issue = ReportIssue(
            id="custom-id",
            title="x",
            severity=IssueSeverity.INFO,
            tags=["disk"],
            scope="/var",
        )
        assert issue.id == "custom-id", "explicit id must not be overwritten"

    def test_rejects_empty_title(self) -> None:
        with pytest.raises(ValidationError):
            ReportIssue(title="", severity=IssueSeverity.INFO)

    def test_rejects_overlong_title(self) -> None:
        with pytest.raises(ValidationError):
            ReportIssue(title="x" * 201, severity=IssueSeverity.INFO)

    def test_rejects_overlong_evidence(self) -> None:
        with pytest.raises(ValidationError):
            ReportIssue(
                title="x",
                severity=IssueSeverity.INFO,
                evidence="x" * 2001,
            )

    def test_rejects_overlong_recommendation(self) -> None:
        with pytest.raises(ValidationError):
            ReportIssue(
                title="x",
                severity=IssueSeverity.INFO,
                recommendation="x" * 1001,
            )

    def test_rejects_unknown_severity(self) -> None:
        with pytest.raises(ValidationError):
            ReportIssue(title="x", severity="nuclear")  # type: ignore[arg-type]

    def test_two_issues_same_tags_scope_share_id(self) -> None:
        a = ReportIssue(title="Disk issue A", severity=IssueSeverity.WARNING, tags=["disk"], scope="/var")
        b = ReportIssue(title="Disk issue B", severity=IssueSeverity.CRITICAL, tags=["disk"], scope="/var")
        assert a.id == b.id, "identical (tag, scope) must diff-match across runs"


# ── ReportPayload ────────────────────────────────────────────────────


class TestReportPayload:
    def test_minimal_valid(self) -> None:
        p = ReportPayload(status=PayloadStatus.HEALTHY, summary="All good.")
        assert p.status == PayloadStatus.HEALTHY
        assert p.summary == "All good."
        assert p.metrics == {}
        assert p.issues == []

    def test_full_payload(self) -> None:
        p = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="Disk at 82% on /var.",
            metrics={"cpu_percent": 23, "memory_percent": 61.4, "disk_percent": 82},
            issues=[
                ReportIssue(
                    title="Disk will be full in 28 days",
                    severity=IssueSeverity.WARNING,
                    evidence="/var grew 4.1GB/week over last 3 runs",
                    recommendation="Rotate /var/log/app/*.log",
                    tags=["disk", "capacity"],
                    scope="/var",
                ),
            ],
        )
        assert len(p.issues) == 1
        assert p.issues[0].id == "disk-var"
        assert p.metrics["cpu_percent"] == 23

    def test_status_unknown_is_valid(self) -> None:
        # UNKNOWN is used when Claude never called submit_report — the evaluator
        # fallback path writes this.
        p = ReportPayload(status=PayloadStatus.UNKNOWN, summary="Run incomplete.")
        assert p.status == PayloadStatus.UNKNOWN

    def test_rejects_empty_summary(self) -> None:
        with pytest.raises(ValidationError):
            ReportPayload(status=PayloadStatus.HEALTHY, summary="")

    def test_rejects_overlong_summary(self) -> None:
        with pytest.raises(ValidationError):
            ReportPayload(status=PayloadStatus.HEALTHY, summary="x" * 501)

    def test_rejects_unknown_status(self) -> None:
        with pytest.raises(ValidationError):
            ReportPayload(status="degraded", summary="x")  # type: ignore[arg-type]

    def test_metrics_accepts_int_float_str(self) -> None:
        p = ReportPayload(
            status=PayloadStatus.HEALTHY,
            summary="ok",
            metrics={"cpu_percent": 23, "load_average_1m": 0.7, "kernel": "6.17.0"},
        )
        assert p.metrics["cpu_percent"] == 23
        assert p.metrics["load_average_1m"] == 0.7
        assert p.metrics["kernel"] == "6.17.0"

    def test_json_roundtrip(self) -> None:
        p = ReportPayload(
            status=PayloadStatus.CRITICAL,
            summary="Disk full.",
            metrics={"disk_percent": 99},
            issues=[
                ReportIssue(
                    title="Disk full on /var",
                    severity=IssueSeverity.CRITICAL,
                    tags=["disk"],
                    scope="/var",
                )
            ],
        )
        data = p.model_dump_json()
        restored = ReportPayload.model_validate_json(data)
        assert restored == p
        assert restored.issues[0].id == "disk-var"

    def test_issues_have_stable_ids_for_set_diff(self) -> None:
        # Simulate two runs producing the "same" issue with different titles.
        run1 = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="run 1",
            issues=[
                ReportIssue(
                    title="Disk filling up quickly",
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                )
            ],
        )
        run2 = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="run 2",
            issues=[
                ReportIssue(
                    title="Disk almost full on /var",  # reworded title
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                )
            ],
        )
        ids1 = {i.id for i in run1.issues}
        ids2 = {i.id for i in run2.issues}
        assert ids1 == ids2, "set-diff must treat reworded same-issue as persisted"


# ── RunMetadata ──────────────────────────────────────────────────────


class TestRunMetadata:
    def test_defaults(self) -> None:
        md = RunMetadata()
        assert md.template_version == ""
        assert md.tool_calls_made == 0
        assert md.runtime_seconds == 0.0

    def test_engine_stamped(self) -> None:
        md = RunMetadata(template_version="server/v3", tool_calls_made=14, runtime_seconds=42.7)
        assert md.template_version == "server/v3"
        assert md.tool_calls_made == 14
        assert md.runtime_seconds == 42.7

    def test_not_part_of_report_payload(self) -> None:
        # RunMetadata is engine-stamped; it must not be accepted as a ReportPayload field.
        # (Pydantic's default behavior ignores extra fields, so this test documents
        # intent rather than blocking — but the absence of the field on the model
        # is what matters.)
        assert "run_metadata" not in ReportPayload.model_fields
        assert "template_version" not in ReportPayload.model_fields
