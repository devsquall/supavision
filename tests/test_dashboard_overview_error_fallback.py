"""When `/dashboard/overview` raises, the user must see a real error message,
not skeleton loaders frozen forever.

Before this fix, an exception in the handler propagated up as a 500, which
the default exception handler converted to a generic branded error page —
but since this endpoint is the HTMX target on the dashboard at `/`, the
swap never happened. The user sat staring at the loading skeleton with no
indication anything had failed.

The fix: catch any exception inside the handler, log it for debugging, and
return a 200 with a fragment template that says "control center failed to
load" and offers a retry button. HTMX swaps on 200, so the error fragment
actually replaces the skeleton.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_dash_err.db")


@pytest.fixture
def session_id(db_path):
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


def test_overview_failure_renders_inline_error_with_retry(db_path, session_id, caplog):
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        # Force the handler to throw. The handler computes total_action_items
        # via store.list_resources(); patch the live store instance directly.
        store = c.app.state.store
        with patch.object(
            store,
            "list_resources",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            r = c.get("/dashboard/overview")
        # Must be 200, not 500 — HTMX won't swap on 5xx by default.
        assert r.status_code == 200, r.text
        body = r.text
        # Real error message, not skeleton.
        assert "Control center failed to load" in body
        assert "Retry" in body
        # And the log captured the full exception for the operator.
        assert any("dashboard_overview handler failed" in rec.message for rec in caplog.records)


def test_overview_happy_path_still_renders(db_path, session_id):
    """Regression — the fallback wrapping mustn't change normal behavior."""
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/dashboard/overview")
        assert r.status_code == 200
        # Normal output doesn't contain the error fragment.
        assert "Control center failed to load" not in r.text
