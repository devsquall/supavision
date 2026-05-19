"""Tests for `supavision backup` — SQLite snapshot CLI.

The README has documented `sqlite3 .supavision/supavision.db ".backup ..."` for
years, but that requires the user to know sqlite. The new `supavision backup`
command wraps sqlite3.Connection.backup() so operators have a one-command
snapshot tool — and the JSON output makes it scriptable for cron-style cron.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from supavision.db import Store
from supavision.models import Resource


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_backup.db")


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "supavision.cli", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_backup_creates_a_valid_sqlite_copy(db_path, tmp_path):
    # Seed something so we can verify it round-trips.
    s = Store(db_path)
    res = Resource(name="src-resource", resource_type="server")
    s.save_resource(res)
    s.close()

    out_path = str(tmp_path / "snapshot.db")
    r = _run_cli("--db", db_path, "--format", "json", "backup", "--output", out_path)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["ok"] is True
    assert data["destination"] == out_path

    # Open the backup directly with sqlite3 and confirm the row is there.
    conn = sqlite3.connect(out_path)
    rows = conn.execute("SELECT data FROM resources").fetchall()
    conn.close()
    assert len(rows) == 1
    assert "src-resource" in rows[0][0]


def test_backup_default_path_uses_timestamp_suffix(db_path):
    Store(db_path).close()
    r = _run_cli("--db", db_path, "--format", "json", "backup")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    # Default destination is `<db>.backup-<UTC-timestamp>`.
    assert data["destination"].startswith(db_path + ".backup-")
    # And the file exists.
    import pathlib

    assert pathlib.Path(data["destination"]).exists()


def test_backup_refuses_to_overwrite_without_force(db_path, tmp_path):
    Store(db_path).close()
    out = tmp_path / "existing.db"
    out.write_bytes(b"DO NOT OVERWRITE")
    r = _run_cli("--db", db_path, "backup", "--output", str(out))
    assert r.returncode != 0
    assert b"DO NOT OVERWRITE" == out.read_bytes(), "backup should NOT have clobbered existing file"
    # _error() reports as JSON on stdout, not stderr.
    assert "Refusing to overwrite" in r.stdout


def test_backup_force_overwrites_existing(db_path, tmp_path):
    s = Store(db_path)
    s.save_resource(Resource(name="real", resource_type="server"))
    s.close()
    out = tmp_path / "existing.db"
    out.write_bytes(b"old content")
    r = _run_cli("--db", db_path, "backup", "--output", str(out), "--force")
    assert r.returncode == 0, r.stderr
    # File now exists as a valid sqlite DB, not the original bytes.
    conn = sqlite3.connect(str(out))
    rows = conn.execute("SELECT data FROM resources").fetchall()
    conn.close()
    assert len(rows) == 1


def test_backup_errors_when_source_db_missing(tmp_path):
    missing = tmp_path / "nope.db"
    r = _run_cli("--db", str(missing), "backup")
    assert r.returncode != 0
    # _error() reports as JSON on stdout, not stderr.
    assert "not found" in r.stdout.lower()


def test_backup_creates_destination_parent_dirs(db_path, tmp_path):
    Store(db_path).close()
    out = tmp_path / "subdir" / "nested" / "snap.db"
    r = _run_cli("--db", db_path, "backup", "--output", str(out))
    assert r.returncode == 0, r.stderr
    assert out.exists()
