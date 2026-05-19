"""Verify `create_app` honors db_path kwarg, SUPAVISION_DB_PATH env var, and the
default fallback (in that order).

Previously `create_app()` had `db_path=".supavision/supavision.db"` as a
positional default, so `uvicorn supavision.web.app:create_app --factory` (the
documented dev command in CLAUDE.md) silently used the local default DB even
when `$SUPAVISION_DB_PATH` was set — confusing for anyone running it outside
the project root or pointing at a separate prod DB.

The fix: `db_path: str | None = None` and explicit precedence:
1. kwarg
2. SUPAVISION_DB_PATH env var
3. .supavision/supavision.db relative to CWD

This file pins the precedence so future refactors can't silently break it.
"""

from __future__ import annotations

import pytest

from supavision.web.app import create_app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with no SUPAVISION_DB_PATH leakage."""
    monkeypatch.delenv("SUPAVISION_DB_PATH", raising=False)


def test_explicit_kwarg_takes_precedence_over_env(tmp_path, monkeypatch):
    kwarg_db = tmp_path / "kwarg.db"
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("SUPAVISION_DB_PATH", str(env_db))

    app = create_app(db_path=str(kwarg_db))

    # Trigger lifespan startup to actually open the DB file.
    from fastapi.testclient import TestClient

    with TestClient(app):
        pass

    assert kwarg_db.exists(), "explicit kwarg DB should have been created"
    assert not env_db.exists(), "env DB must not be touched when kwarg is set"


def test_env_var_used_when_no_kwarg(tmp_path, monkeypatch):
    env_db = tmp_path / "from_env.db"
    monkeypatch.setenv("SUPAVISION_DB_PATH", str(env_db))

    app = create_app()  # no kwarg

    from fastapi.testclient import TestClient

    with TestClient(app):
        pass

    assert env_db.exists(), "SUPAVISION_DB_PATH should have been honored"


def test_falls_back_to_default_path_relative_to_cwd(tmp_path, monkeypatch):
    # Move CWD to a tmp dir so we don't write into the real project dir.
    monkeypatch.chdir(tmp_path)

    app = create_app()  # no kwarg, no env

    from fastapi.testclient import TestClient

    with TestClient(app):
        pass

    expected = tmp_path / ".supavision" / "supavision.db"
    assert expected.exists(), f"default DB should have landed at {expected}"


def test_empty_env_var_falls_back_to_default(tmp_path, monkeypatch):
    """SUPAVISION_DB_PATH="" is treated as 'unset' — not 'use empty path'."""
    monkeypatch.setenv("SUPAVISION_DB_PATH", "")
    monkeypatch.chdir(tmp_path)

    app = create_app()

    from fastapi.testclient import TestClient

    with TestClient(app):
        pass

    expected = tmp_path / ".supavision" / "supavision.db"
    assert expected.exists()
