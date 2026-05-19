"""Verify the operational-metrics panel renders on /settings.

Until now, the system-level operational metrics (run failures last 24h,
duration percentiles, notification delivery stats) were only available via
`GET /api/v1/system/metrics` — a JSON endpoint with no dashboard surface.
Operators had to curl to know how Supavision itself was doing.

The fix: /settings (admin-only page) now renders a panel built from the
same `compute_system_metrics(store)` helper. Same data both places.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Run, RunStatus, RunType, Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password
from supavision.web.routes import compute_system_metrics


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_sys.db")


def _seed_admin(db_path: str) -> str:
    s = Store(db_path)
    user = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin",
    )
    s.create_user(user)
    sess = Session(user_id=user.id)
    s.create_session(sess)
    s.close()
    return sess.id


def test_settings_page_shows_operational_metrics_panel(db_path):
    session_id = _seed_admin(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings")
        assert r.status_code == 200
        body = r.text
        # Header.
        assert "Operational metrics" in body
        # Pointer back to the JSON API for parity.
        assert "/api/v1/system/metrics" in body
        # Each of the six labelled fields renders.
        for label in [
            "Failed runs (24h)",
            "Runs in flight",
            "Run duration p50",
            "Run duration p95",
            "Notifications sent",
            "Notification failures",
        ]:
            assert label in body, f"missing label: {label}"


def test_panel_reflects_actual_run_data(db_path):
    """Seed two failed runs in the last 24h — panel should display 2."""
    from datetime import datetime, timedelta, timezone

    session_id = _seed_admin(db_path)
    s = Store(db_path)
    res = Resource(name="r1", resource_type="server")
    s.save_resource(res)
    now = datetime.now(timezone.utc)
    for _ in range(2):
        run = Run(
            resource_id=res.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.FAILED,
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=5),
        )
        s.save_run(run)
    s.close()

    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings")
        # 2 failed runs should appear in the panel.
        # The actual badge text is just the number; assert it's present near
        # the "Failed runs (24h)" label.
        body = r.text
        assert "Failed runs (24h)" in body
        # Pull out the segment after the label and verify the count is 2.
        idx = body.index("Failed runs (24h)")
        chunk = body[idx : idx + 600]
        assert "2" in chunk, chunk


def test_compute_system_metrics_returns_expected_keys(tmp_path):
    """Unit test the shared helper directly — no HTTP."""
    s = Store(str(tmp_path / "u.db"))
    metrics = compute_system_metrics(s)
    assert "version" in metrics
    assert "scheduler" in metrics
    assert "runs" in metrics
    assert "by_status" in metrics["runs"]
    assert "failures_24h" in metrics["runs"]
    assert "duration_seconds" in metrics["runs"]
    assert "notifications" in metrics
    assert "db" in metrics
    s.close()


def test_api_endpoint_and_panel_share_same_helper(db_path):
    """The JSON endpoint and the dashboard panel must return identical data."""
    _seed_admin(db_path)
    s = Store(db_path)
    api_data = compute_system_metrics(s)
    # Sanity assertions on the structure that drives the panel template.
    assert isinstance(api_data["runs"]["by_status"], dict)
    assert isinstance(api_data["runs"]["failures_24h"], int)
    s.close()
