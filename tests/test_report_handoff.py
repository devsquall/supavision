"""Tests for Workstream A2: structured report handoff mechanism.

Covers:
- allocate_payload_path / cleanup_payload_path
- read_payload (valid, missing, empty, malformed JSON, schema-invalid)
- read_payload_from_dict
- build_preamble content
- supports_structured_payload gating (server-only in A2)
- ToolDispatcher.submit_report handling (valid, invalid, repeat calls)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supavision.executor import Executor
from supavision.models.health import PayloadStatus, ReportPayload
from supavision.report_handoff import (
    allocate_payload_path,
    build_preamble,
    cleanup_payload_path,
    read_payload,
    read_payload_from_dict,
)
from supavision.report_vocab import (
    REPORT_VOCAB,
    get_vocabulary,
    supports_structured_payload,
)
from supavision.tools import TOOL_DEFINITIONS, ToolDispatcher

# ── Path allocation ─────────────────────────────────────────────────


class TestAllocatePayloadPath:
    def test_deterministic_on_run_id(self) -> None:
        p1 = allocate_payload_path("run-abc")
        p2 = allocate_payload_path("run-abc")
        assert p1 == p2

    def test_distinct_per_run(self) -> None:
        assert allocate_payload_path("run-a") != allocate_payload_path("run-b")

    def test_parent_exists(self) -> None:
        p = allocate_payload_path("run-test-parent")
        assert p.parent.exists()

    def test_cleanup_missing_file_is_safe(self, tmp_path: Path) -> None:
        # Should not raise even if the file doesn't exist.
        cleanup_payload_path(tmp_path / "nonexistent.json")


# ── Reading ─────────────────────────────────────────────────────────


class TestReadPayload:
    def _write(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_payload(tmp_path / "nope.json") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        self._write(path, "")
        assert read_payload(path) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "ws.json"
        self._write(path, "   \n\t  ")
        assert read_payload(path) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        self._write(path, "{not json")
        assert read_payload(path) is None

    def test_missing_required_field_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "invalid.json"
        self._write(path, json.dumps({"status": "healthy"}))  # no summary
        assert read_payload(path) is None

    def test_valid_payload_parses(self, tmp_path: Path) -> None:
        path = tmp_path / "good.json"
        self._write(
            path,
            json.dumps(
                {
                    "status": "warning",
                    "summary": "Disk at 82%.",
                    "metrics": {"disk_percent": 82},
                    "issues": [
                        {
                            "title": "Disk filling up on /var",
                            "severity": "warning",
                            "tags": ["disk"],
                            "scope": "/var",
                            "evidence": "df -h shows 82%",
                            "recommendation": "rotate logs",
                        }
                    ],
                }
            ),
        )
        payload = read_payload(path)
        assert payload is not None
        assert payload.status == PayloadStatus.WARNING
        assert payload.issues[0].id == "disk-var"

    def test_unknown_status_accepted_when_explicit(self, tmp_path: Path) -> None:
        # UNKNOWN is a valid status (engine writes it on fallback paths).
        path = tmp_path / "unknown.json"
        self._write(path, json.dumps({"status": "unknown", "summary": "incomplete"}))
        payload = read_payload(path)
        assert payload is not None
        assert payload.status == PayloadStatus.UNKNOWN


class TestReadPayloadFromDict:
    def test_valid(self) -> None:
        p = read_payload_from_dict({"status": "healthy", "summary": "ok"})
        assert p is not None
        assert p.status == PayloadStatus.HEALTHY

    def test_invalid_returns_none(self) -> None:
        assert read_payload_from_dict({"status": "boom"}) is None
        assert read_payload_from_dict({}) is None


# ── Preamble ────────────────────────────────────────────────────────


class TestPreamble:
    def test_contains_payload_path(self, tmp_path: Path) -> None:
        path = tmp_path / "r.json"
        vocab = get_vocabulary("server")
        assert vocab is not None
        text = build_preamble(path, vocab, "server")
        assert str(path) in text

    def test_contains_tag_vocabulary(self) -> None:
        vocab = get_vocabulary("server")
        assert vocab is not None
        text = build_preamble(Path("/tmp/x.json"), vocab, "server")
        for tag in vocab.tag_list:
            assert f"`{tag}`" in text

    def test_contains_metric_names(self) -> None:
        vocab = get_vocabulary("server")
        assert vocab is not None
        text = build_preamble(Path("/tmp/x.json"), vocab, "server")
        # Server metric schema includes cpu_percent and disk_percent.
        assert "cpu_percent" in text
        assert "disk_percent" in text

    def test_instructs_single_submission(self) -> None:
        vocab = get_vocabulary("server")
        assert vocab is not None
        text = build_preamble(Path("/tmp/x.json"), vocab, "server")
        assert "exactly once" in text.lower() or "exactly once" in text.lower()

    def test_instructs_canonical_tags(self) -> None:
        vocab = get_vocabulary("server")
        assert vocab is not None
        text = build_preamble(Path("/tmp/x.json"), vocab, "server")
        assert "stable ID" in text or "stable id" in text.lower()


# ── Vocabulary gating ───────────────────────────────────────────────


class TestVocabularyGating:
    def test_server_opted_in(self) -> None:
        assert supports_structured_payload("server")
        assert get_vocabulary("server") is not None

    def test_a8_rollout_opted_in(self) -> None:
        # A8 opted these in (they share the shared preamble + file handoff).
        assert supports_structured_payload("aws_account")
        assert supports_structured_payload("github_org")
        assert supports_structured_payload("database_postgresql")
        assert supports_structured_payload("database_mysql")

    def test_unknown_type_not_opted_in(self) -> None:
        assert not supports_structured_payload("made_up_type")
        assert get_vocabulary("made_up_type") is None

    def test_server_has_expected_tags(self) -> None:
        vocab = REPORT_VOCAB["server"]
        # These are the tags Claude will be told to pick from on real runs.
        expected = {"disk", "memory", "cpu", "service", "security", "cert-expiry"}
        assert expected.issubset(set(vocab.tag_list))


# ── submit_report tool ──────────────────────────────────────────────


class TestSubmitReportTool:
    def test_is_in_tool_definitions(self) -> None:
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "submit_report" in names

    def test_schema_has_required_fields(self) -> None:
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "submit_report")
        required = set(tool["input_schema"]["required"])
        # status + summary are required; issues/metrics optional per schema
        assert "status" in required
        assert "summary" in required

    @pytest.mark.asyncio
    async def test_dispatch_valid_payload(self) -> None:
        dispatcher = ToolDispatcher(executor=Executor(connection=None))
        result = await dispatcher.dispatch(
            "submit_report",
            {
                "status": "healthy",
                "summary": "All green.",
                "metrics": {"cpu_percent": 23},
                "issues": [],
            },
        )
        assert "OK" in result
        assert dispatcher.submitted_payload is not None
        assert dispatcher.submitted_payload.status == PayloadStatus.HEALTHY
        assert dispatcher.submit_report_call_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_invalid_payload_returns_error(self) -> None:
        dispatcher = ToolDispatcher(executor=Executor(connection=None))
        result = await dispatcher.dispatch(
            "submit_report",
            {"status": "boom", "summary": "x"},
        )
        assert "VALIDATION ERROR" in result
        assert dispatcher.submitted_payload is None

    @pytest.mark.asyncio
    async def test_repeat_calls_last_write_wins(self) -> None:
        dispatcher = ToolDispatcher(executor=Executor(connection=None))
        await dispatcher.dispatch(
            "submit_report",
            {"status": "healthy", "summary": "first"},
        )
        await dispatcher.dispatch(
            "submit_report",
            {"status": "critical", "summary": "second"},
        )
        assert dispatcher.submit_report_call_count == 2
        assert dispatcher.submitted_payload is not None
        assert dispatcher.submitted_payload.status == PayloadStatus.CRITICAL
        assert dispatcher.submitted_payload.summary == "second"

    @pytest.mark.asyncio
    async def test_dispatch_invalid_then_valid_retry(self) -> None:
        # R4: Claude can retry after an invalid submission within the same loop.
        dispatcher = ToolDispatcher(executor=Executor(connection=None))
        bad = await dispatcher.dispatch(
            "submit_report",
            {"status": "bogus"},  # missing summary too
        )
        assert "VALIDATION ERROR" in bad
        assert dispatcher.submitted_payload is None
        ok = await dispatcher.dispatch(
            "submit_report",
            {"status": "warning", "summary": "recovered"},
        )
        assert "OK" in ok
        assert dispatcher.submitted_payload is not None
        assert dispatcher.submitted_payload.status == PayloadStatus.WARNING

    def test_issue_id_stable_across_submissions(self) -> None:
        # Two payloads describing the same issue with different wording
        # must produce the same issue id for diff stability.
        p1 = ReportPayload.model_validate(
            {
                "status": "warning",
                "summary": "disk",
                "issues": [
                    {
                        "title": "Disk filling up quickly",
                        "severity": "warning",
                        "tags": ["disk"],
                        "scope": "/var",
                    }
                ],
            }
        )
        p2 = ReportPayload.model_validate(
            {
                "status": "warning",
                "summary": "disk",
                "issues": [
                    {
                        "title": "Disk almost full on /var",  # reworded
                        "severity": "critical",  # escalated, but same id
                        "tags": ["disk"],
                        "scope": "/var",
                    }
                ],
            }
        )
        assert p1.issues[0].id == p2.issues[0].id
