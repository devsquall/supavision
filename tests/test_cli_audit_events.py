"""Verify CLI admin actions write audit log entries.

The dashboard handlers for these actions are audit-logged (Phase 9 work);
the CLI counterparts weren't. That meant an operator who used the CLI
(or an attacker who got a shell on the host) could create resources and
API keys with no audit trail.

These tests invoke the CLI via subprocess and then read the auth_audit_log
table directly to confirm the event landed.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from supavision.db import Store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_cli_audit.db")


def _run_cli(db_path: str, *args: str) -> subprocess.CompletedProcess:
    """Invoke supavision CLI in-process via python -m supavision.cli."""
    return subprocess.run(
        [sys.executable, "-m", "supavision.cli", "--db", db_path, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_api_key_create_writes_audit_event(db_path):
    r = _run_cli(db_path, "api-key-create", "--label", "my-key")
    assert r.returncode == 0, r.stderr

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="api_key_created")
    s.close()
    assert len(events) == 1
    detail = events[0]["detail"]
    assert "source=cli" in detail
    assert "my-key" in detail


def test_cli_api_key_revoke_writes_audit_event(db_path):
    # First create a key — also writes an audit event, ignored here.
    create = _run_cli(db_path, "--format", "json", "api-key-create", "--label", "doomed")
    assert create.returncode == 0
    # Pull the key_id out of the JSON stdout.
    import json

    data = json.loads(create.stdout)
    key_id = data["key_id"]

    revoke = _run_cli(db_path, "api-key-revoke", key_id)
    assert revoke.returncode == 0

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="api_key_revoked")
    s.close()
    assert len(events) == 1
    assert key_id in events[0]["detail"]
    assert "source=cli" in events[0]["detail"]


def test_cli_resource_add_writes_audit_event(db_path):
    r = _run_cli(
        db_path,
        "resource-add",
        "prod-via-cli",
        "--type",
        "server",
        "--config",
        "ssh_host=10.0.0.1",
        "ssh_user=ops",
    )
    assert r.returncode == 0, r.stderr

    s = Store(db_path)
    events, _ = s.list_auth_events_paginated(event="resource_created")
    s.close()
    assert len(events) == 1
    detail = events[0]["detail"]
    assert "prod-via-cli" in detail
    assert "server" in detail
    assert "source=cli" in detail
