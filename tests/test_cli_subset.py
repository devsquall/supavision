"""Tests for cli.py — subset of CLI commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from supavision.db import Store
from supavision.models import Resource


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temp database."""
    db_path = tmp_path / "test.db"
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def db_path(tmp_path):
    """Return a path string for a temporary database."""
    return str(tmp_path / "test.db")


def _run_cli(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run the supavision CLI as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "supavision.cli", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


# ── --version ───────────────────────────────────────────────────


class TestVersion:
    def test_version_flag_prints_version_and_exits(self):
        result = _run_cli("--version")
        assert result.returncode == 0
        assert "supavision" in result.stdout.lower()
        # Version string should contain a semver-like pattern
        import re
        assert re.search(r"\d+\.\d+\.\d+", result.stdout)  # Match any semver


# ── mcp-config ──────────────────────────────────────────────────


class TestMcpConfig:
    def test_mcp_config_outputs_valid_json(self, db_path):
        result = _run_cli("--db", db_path, "mcp-config")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "mcpServers" in data

    def test_mcp_config_includes_correct_python_executable(self, db_path):
        result = _run_cli("--db", db_path, "mcp-config")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        command = data["mcpServers"]["supavision"]["command"]
        assert command == sys.executable

    def test_mcp_config_includes_correct_args(self, db_path):
        result = _run_cli("--db", db_path, "mcp-config")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        args = data["mcpServers"]["supavision"]["args"]
        assert args == ["-m", "supavision.mcp"]


# ── set-schedule ────────────────────────────────────────────────


class TestSetSchedule:
    def test_set_schedule_invalid_cron_fails(self, db_path):
        """An invalid cron expression should cause a non-zero exit."""
        # First create a resource so the schedule command has something to target
        store = Store(db_path)
        r = _make_resource()
        store.save_resource(r)
        store.close()

        result = _run_cli("--db", db_path, "set-schedule", r.id, "--discovery", "not a cron")
        assert result.returncode != 0
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert "invalid" in output["error"].lower() or "cron" in output["error"].lower()

    def test_set_schedule_valid_cron_succeeds(self, db_path):
        """A valid cron expression should succeed."""
        store = Store(db_path)
        r = _make_resource()
        store.save_resource(r)
        store.close()

        result = _run_cli("--db", db_path, "set-schedule", r.id, "--discovery", "*/5 * * * *")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert output["command"] == "set_schedule"
        assert output["resource_id"] == r.id


# ── resource-add ────────────────────────────────────────────────


class TestResourceAdd:
    def test_resource_add_missing_name_exits_with_error(self, db_path):
        """Calling resource-add without a name argument should fail."""
        result = _run_cli("--db", db_path, "resource-add", "--type", "server")
        # argparse exits with code 2 for missing required positional args
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_resource_add_creates_resource(self, db_path):
        """resource-add with valid args should create a resource in the DB."""
        result = _run_cli("--db", db_path, "resource-add", "my-server", "--type", "server")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert output["command"] == "resource_add"
        assert output["name"] == "my-server"
        assert "resource_id" in output

        # Verify it persisted in the database
        store = Store(db_path)
        r = store.get_resource(output["resource_id"])
        assert r is not None
        assert r.name == "my-server"
        assert r.resource_type == "server"
        store.close()


# ── doctor ──────────────────────────────────────────────────────


class TestDoctor:
    def test_doctor_runs_and_outputs_checks(self, db_path):
        """doctor should run without crashing and output check results."""
        result = _run_cli("--db", db_path, "doctor")
        # doctor may exit 0 (all ok) or 1 (some checks failed), both are valid
        output = json.loads(result.stdout)
        assert output["command"] == "doctor"
        assert "checks" in output
        assert isinstance(output["checks"], list)
        assert len(output["checks"]) > 0

        # Each check should have the expected structure
        for check in output["checks"]:
            assert "check" in check
            assert "ok" in check
            assert "detail" in check

        # stderr should contain human-readable output with OK/FAIL markers
        assert "OK" in result.stderr or "FAIL" in result.stderr


# ── purge --dry-run ─────────────────────────────────────────────


class TestPurgeDryRun:
    def test_purge_dry_run_shows_counts_without_deleting(self, db_path):
        """purge --dry-run should report counts but not actually delete data."""
        # Seed the database with a resource so the store is initialized
        store = Store(db_path)
        r = _make_resource()
        store.save_resource(r)
        store.close()

        result = _run_cli("--db", db_path, "purge", "--dry-run")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert output["command"] == "purge"
        assert output["dry_run"] is True
        assert "reports" in output
        assert "runs" in output
        assert isinstance(output["reports"], int)
        assert isinstance(output["runs"], int)
        # stderr should mention "dry run" or "would delete"
        assert "dry run" in result.stderr.lower() or "would delete" in result.stderr.lower()
