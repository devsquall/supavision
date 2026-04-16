"""Tests for Workstream A3: persistence of ReportPayload + RunMetadata on Report.

Verifies that:
- Pre-A reports (no payload) still round-trip cleanly (R5 in the plan)
- Reports with a structured payload round-trip all fields
- The Store (SQLite) correctly persists and reloads the nested payload
- Legacy JSON blobs (without `payload` / `run_metadata` keys) load as None
"""

from __future__ import annotations

import json
from pathlib import Path

from supavision.db import Store
from supavision.models import Report, Resource, RunType
from supavision.models.health import (
    IssueSeverity,
    PayloadStatus,
    ReportIssue,
    ReportPayload,
    RunMetadata,
)


def _make_store(tmp_path: Path) -> Store:
    return Store(db_path=str(tmp_path / "test.db"))


def _make_resource(store: Store, rtype: str = "server") -> Resource:
    r = Resource(name="test", resource_type=rtype, config={})
    store.save_resource(r)
    return r


# ── Pydantic model roundtrip ─────────────────────────────────────────


class TestReportModelRoundtrip:
    def test_legacy_report_no_payload(self) -> None:
        r = Report(
            resource_id="r1",
            run_type=RunType.HEALTH_CHECK,
            content="prose only",
        )
        assert r.payload is None
        assert r.run_metadata is None
        data = r.model_dump(mode="json")
        restored = Report.model_validate(data)
        assert restored.payload is None
        assert restored.run_metadata is None
        assert restored.content == "prose only"

    def test_structured_report_roundtrip(self) -> None:
        payload = ReportPayload(
            status=PayloadStatus.WARNING,
            summary="Disk filling on /var",
            metrics={"disk_percent": 82, "cpu_percent": 23},
            issues=[
                ReportIssue(
                    title="Disk will be full in 28 days",
                    severity=IssueSeverity.WARNING,
                    tags=["disk"],
                    scope="/var",
                    evidence="df -h shows 82%",
                    recommendation="rotate /var/log/app/*.log",
                )
            ],
        )
        metadata = RunMetadata(
            template_version="server/v1",
            tool_calls_made=14,
            runtime_seconds=42.7,
        )
        r = Report(
            resource_id="r1",
            run_type=RunType.HEALTH_CHECK,
            content="prose",
            payload=payload,
            run_metadata=metadata,
        )
        data = r.model_dump(mode="json")
        restored = Report.model_validate(data)
        assert restored.payload is not None
        assert restored.payload.status == PayloadStatus.WARNING
        assert restored.payload.issues[0].id == "disk-var"
        assert restored.run_metadata is not None
        assert restored.run_metadata.template_version == "server/v1"
        assert restored.run_metadata.tool_calls_made == 14

    def test_parse_pre_A_dict_ignores_missing_keys(self) -> None:
        """R5: an old DB row without payload/run_metadata keys loads cleanly."""
        legacy = {
            "id": "abc",
            "resource_id": "r1",
            "run_type": "health_check",
            "content": "old prose",
            "status": "completed",
            "error": None,
            "created_at": "2026-04-10T12:00:00+00:00",
        }
        r = Report.model_validate(legacy)
        assert r.payload is None
        assert r.run_metadata is None
        assert r.content == "old prose"


# ── Store persistence roundtrip ──────────────────────────────────────


class TestStorePersistence:
    def test_legacy_report_persists_and_reloads(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        res = _make_resource(store)
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="legacy",
        )
        store.save_report(r)
        loaded = store.get_report(r.id)
        assert loaded is not None
        assert loaded.payload is None
        assert loaded.run_metadata is None
        assert loaded.content == "legacy"

    def test_structured_report_persists_and_reloads(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        res = _make_resource(store)
        payload = ReportPayload(
            status=PayloadStatus.CRITICAL,
            summary="/var full",
            metrics={"disk_percent": 99},
            issues=[
                ReportIssue(
                    title="Disk full",
                    severity=IssueSeverity.CRITICAL,
                    tags=["disk"],
                    scope="/var",
                    evidence="df shows 100%",
                    recommendation="free space now",
                ),
                ReportIssue(
                    title="OOM risk",
                    severity=IssueSeverity.WARNING,
                    tags=["memory"],
                    scope="host",
                ),
            ],
        )
        metadata = RunMetadata(template_version="server/v1", tool_calls_made=7, runtime_seconds=12.3)
        r = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="prose",
            payload=payload,
            run_metadata=metadata,
        )
        store.save_report(r)

        loaded = store.get_report(r.id)
        assert loaded is not None
        assert loaded.payload is not None
        assert loaded.payload.status == PayloadStatus.CRITICAL
        assert len(loaded.payload.issues) == 2
        assert loaded.payload.issues[0].id == "disk-var"
        assert loaded.payload.issues[1].id == "memory-host"
        assert loaded.payload.metrics["disk_percent"] == 99
        assert loaded.run_metadata is not None
        assert loaded.run_metadata.template_version == "server/v1"
        assert loaded.run_metadata.tool_calls_made == 7

    def test_legacy_json_row_loads_as_none_payload(self, tmp_path: Path) -> None:
        """Directly insert a legacy-shaped row and confirm it loads without error."""
        store = _make_store(tmp_path)
        res = _make_resource(store)
        legacy_data = {
            "id": "legacy-1",
            "resource_id": res.id,
            "run_type": "health_check",
            "content": "legacy prose",
            "status": "completed",
            "error": None,
            "created_at": "2026-04-01T10:00:00+00:00",
        }
        store._execute(  # type: ignore[attr-defined]
            "INSERT INTO reports (id, resource_id, run_type, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-1",
                res.id,
                "health_check",
                json.dumps(legacy_data),
                "2026-04-01T10:00:00+00:00",
            ),
        )
        store._commit()  # type: ignore[attr-defined]

        loaded = store.get_report("legacy-1")
        assert loaded is not None
        assert loaded.payload is None
        assert loaded.run_metadata is None
        assert loaded.content == "legacy prose"

    def test_recent_reports_mixed_legacy_and_new(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        res = _make_resource(store)
        # Legacy
        r1 = Report(resource_id=res.id, run_type=RunType.HEALTH_CHECK, content="old")
        store.save_report(r1)
        # New
        r2 = Report(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            content="new",
            payload=ReportPayload(status=PayloadStatus.HEALTHY, summary="ok"),
        )
        store.save_report(r2)

        recent = store.get_recent_reports(res.id, RunType.HEALTH_CHECK, limit=5)
        assert len(recent) == 2
        # Both shapes round-trip
        for rpt in recent:
            if rpt.content == "old":
                assert rpt.payload is None
            else:
                assert rpt.payload is not None
                assert rpt.payload.status == PayloadStatus.HEALTHY
