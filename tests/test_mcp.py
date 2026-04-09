"""Tests for mcp.py — MCP JSON-RPC server over stdio."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from supavision import __version__
from supavision.db import Store
from supavision.mcp import handle_jsonrpc
from supavision.models import (
    Checklist,
    ChecklistItem,
    Evaluation,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
    Severity,
    SystemContext,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    """Return a path for the test database."""
    return tmp_path / "test.db"


@pytest.fixture
def store(db_path):
    """Create a writable Store to populate test data."""
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def ro_conn(db_path, store):
    """Read-only sqlite3 connection for the MCP handler.

    Depends on ``store`` so the schema is already created before we open
    a read-only connection.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    yield conn
    conn.close()


def _jsonrpc(method: str, params: dict | None = None, msg_id: int = 1) -> str:
    """Build a JSON-RPC request string."""
    msg: dict = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


# ── Helper factories ────────────────────────────────────────────────


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


def _make_report(resource_id: str, run_type: RunType = RunType.HEALTH_CHECK, content: str = "Report content") -> Report:
    return Report(resource_id=resource_id, run_type=run_type, content=content)


def _make_evaluation(report_id: str, resource_id: str, severity: Severity = Severity.HEALTHY, summary: str = "All good") -> Evaluation:
    return Evaluation(
        report_id=report_id,
        resource_id=resource_id,
        severity=severity,
        summary=summary,
        should_alert=False,
    )


def _make_context(resource_id: str, version: int = 1, content: str = "context data") -> SystemContext:
    return SystemContext(resource_id=resource_id, content=content, version=version)


def _make_checklist(resource_id: str, version: int = 1) -> Checklist:
    items = [
        ChecklistItem(description="Check disk usage", source="discovery"),
        ChecklistItem(description="Verify nginx running", source="discovery"),
    ]
    return Checklist(resource_id=resource_id, items=items, version=version)


def _make_run(
    resource_id: str,
    run_type: RunType = RunType.HEALTH_CHECK,
    status: RunStatus = RunStatus.COMPLETED,
) -> Run:
    return Run(
        resource_id=resource_id,
        run_type=run_type,
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        completed_at=datetime.now(timezone.utc),
    )


# ── Protocol-level tests ───────────────────────────────────────────


class TestInitialize:
    def test_initialize_returns_protocol_version_and_server_info(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("initialize"))
        assert resp is not None
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"]["name"] == "supavision"
        assert result["serverInfo"]["version"] == __version__

    def test_notifications_initialized_returns_none(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("notifications/initialized"))
        assert resp is None


# ── tools/list ──────────────────────────────────────────────────────


class TestToolsList:
    def test_tools_list_returns_exactly_4_tools(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/list"))
        assert resp is not None
        tools = resp["result"]["tools"]
        assert len(tools) == 11  # 4 health + 2 metrics + 5 work

    def test_each_tool_has_correct_structure(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/list"))
        tools = resp["result"]["tools"]

        expected_names = {
            # Lane 1: Health
            "supavision_list_resources",
            "supavision_get_latest_report",
            "supavision_get_baseline",
            "supavision_get_run_history",
            # Metrics
            "supavision_get_metrics",
            "supavision_get_metrics_trend",
            # Lane 2: Work
            "supavision_list_findings",
            "supavision_get_finding",
            "supavision_get_project_stats",
            "supavision_list_blocklist",
            "supavision_search_findings",
        }
        actual_names = {t["name"] for t in tools}
        assert actual_names == expected_names

        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert isinstance(tool["description"], str)
            assert len(tool["description"]) > 0
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


# ── supavision_list_resources ───────────────────────────────────────


class TestListResources:
    def test_list_resources_returns_data(self, store, ro_conn):
        r1 = _make_resource(name="web-server")
        r2 = _make_resource(name="db-server", resource_type="database")
        store.save_resource(r1)
        store.save_resource(r2)

        # Add an evaluation for r1 so severity is populated
        report = _make_report(r1.id)
        store.save_report(report)
        ev = _make_evaluation(report.id, r1.id, severity=Severity.WARNING, summary="Disk high")
        store.save_evaluation(ev)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_list_resources",
            "arguments": {},
        }))
        assert resp is not None
        assert "error" not in resp
        content = resp["result"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

        resources = json.loads(content[0]["text"])
        assert len(resources) == 2
        names = {r["name"] for r in resources}
        assert names == {"web-server", "db-server"}

        # r1 should have severity from the evaluation
        by_name = {r["name"]: r for r in resources}
        assert by_name["web-server"]["severity"] == "warning"
        # r2 has no evaluation
        assert by_name["db-server"]["severity"] is None

    def test_list_resources_empty_db(self, store, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_list_resources",
            "arguments": {},
        }))
        content = resp["result"]["content"]
        resources = json.loads(content[0]["text"])
        assert resources == []


# ── supavision_get_latest_report ────────────────────────────────────


class TestGetLatestReport:
    def test_get_latest_report_with_valid_resource(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        report = _make_report(resource.id, content="Everything looks fine")
        store.save_report(report)

        ev = _make_evaluation(report.id, resource.id, severity=Severity.HEALTHY, summary="All clear")
        store.save_evaluation(ev)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_latest_report",
            "arguments": {"resource_id": resource.id},
        }))
        assert "error" not in resp
        data = json.loads(resp["result"]["content"][0]["text"])
        assert data["report_content"] == "Everything looks fine"
        assert data["severity"] == "healthy"
        assert data["summary"] == "All clear"
        assert data["run_type"] == "health_check"

    def test_get_latest_report_nonexistent_resource(self, store, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_latest_report",
            "arguments": {"resource_id": "nonexistent-id"},
        }))
        assert "error" not in resp  # No JSON-RPC error — tool returns an error message in content
        data = json.loads(resp["result"]["content"][0]["text"])
        assert "error" in data
        assert "No reports found" in data["error"]


# ── supavision_get_baseline ─────────────────────────────────────────


class TestGetBaseline:
    def test_get_baseline_returns_context_and_checklist(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        ctx = _make_context(resource.id, version=1, content="System context document")
        store.save_context(ctx)

        cl = _make_checklist(resource.id, version=1)
        store.save_checklist(cl)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_baseline",
            "arguments": {"resource_id": resource.id},
        }))
        assert "error" not in resp
        data = json.loads(resp["result"]["content"][0]["text"])
        assert data["system_context"] == "System context document"
        assert data["version"] == 1
        assert len(data["checklist_items"]) == 2
        assert "Check disk usage" in data["checklist_items"]
        assert "Verify nginx running" in data["checklist_items"]

    def test_get_baseline_no_discovery_yet(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_baseline",
            "arguments": {"resource_id": resource.id},
        }))
        data = json.loads(resp["result"]["content"][0]["text"])
        assert "error" in data
        assert "No baseline found" in data["error"]


# ── supavision_get_run_history ──────────────────────────────────────


class TestGetRunHistory:
    def test_get_run_history_returns_runs(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(3):
            run = Run(
                resource_id=resource.id,
                run_type=RunType.HEALTH_CHECK,
                status=RunStatus.COMPLETED,
                started_at=datetime.now(timezone.utc) - timedelta(hours=3 - i),
                completed_at=datetime.now(timezone.utc) - timedelta(hours=3 - i, minutes=-10),
                created_at=datetime.now(timezone.utc) - timedelta(hours=3 - i),
            )
            store.save_run(run)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_run_history",
            "arguments": {"resource_id": resource.id},
        }))
        assert "error" not in resp
        runs = json.loads(resp["result"]["content"][0]["text"])
        assert len(runs) == 3
        for run in runs:
            assert run["run_type"] == "health_check"
            assert run["status"] == "completed"
            assert run["started_at"] is not None

    def test_get_run_history_respects_limit(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        for i in range(10):
            run = Run(
                resource_id=resource.id,
                run_type=RunType.HEALTH_CHECK,
                status=RunStatus.COMPLETED,
                started_at=datetime.now(timezone.utc) - timedelta(hours=10 - i),
                completed_at=datetime.now(timezone.utc) - timedelta(hours=10 - i, minutes=-5),
                created_at=datetime.now(timezone.utc) - timedelta(hours=10 - i),
            )
            store.save_run(run)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_run_history",
            "arguments": {"resource_id": resource.id, "limit": 3},
        }))
        runs = json.loads(resp["result"]["content"][0]["text"])
        assert len(runs) == 3

    def test_get_run_history_no_runs(self, store, ro_conn):
        resource = _make_resource()
        store.save_resource(resource)

        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "supavision_get_run_history",
            "arguments": {"resource_id": resource.id},
        }))
        data = json.loads(resp["result"]["content"][0]["text"])
        assert "error" in data
        assert "No run history found" in data["error"]


# ── Error handling ──────────────────────────────────────────────────


class TestErrors:
    def test_unknown_tool_returns_jsonrpc_error(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32601
        assert "Unknown tool" in resp["error"]["message"]

    def test_unknown_method_returns_jsonrpc_error(self, ro_conn):
        resp = handle_jsonrpc(ro_conn, _jsonrpc("some/unknown_method"))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32601
        assert "Unknown method" in resp["error"]["message"]

    def test_malformed_json_raises_decode_error(self, ro_conn):
        with pytest.raises(json.JSONDecodeError):
            handle_jsonrpc(ro_conn, "this is not json{{{")
