"""Verify the API keys table on /settings shows each key's role.

Before this fix, the API keys table displayed Label / Created / Last used /
Status. The role (admin vs viewer) — which controls whether the key can
mutate state via `/api/v1/*` — was invisible. A mistakenly-created admin
key looked identical to a viewer key, so operators had no way to audit
their attack surface without sqlite.

The fix: include `role` in `Store.list_api_keys()` and render it as a
badge in the settings table.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Session, User
from supavision.web.app import create_app
from supavision.web.auth import generate_api_key, hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_keys.db")


def _seed_admin_and_keys(db_path: str) -> str:
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

    # Two keys: one admin, one viewer.
    admin_id, _raw, admin_hash = generate_api_key()
    s.save_api_key(admin_id, admin_hash, label="ops-bot", role="admin")
    viewer_id, _raw, viewer_hash = generate_api_key()
    s.save_api_key(viewer_id, viewer_hash, label="read-dashboard", role="viewer")
    s.close()
    return sess.id


def test_list_api_keys_includes_role(tmp_path):
    s = Store(str(tmp_path / "u.db"))
    aid, _r, ah = generate_api_key()
    s.save_api_key(aid, ah, label="k1", role="admin")
    vid, _r, vh = generate_api_key()
    s.save_api_key(vid, vh, label="k2", role="viewer")
    keys = s.list_api_keys()
    roles = {k["label"]: k["role"] for k in keys}
    assert roles == {"k1": "admin", "k2": "viewer"}
    s.close()


def test_settings_table_renders_role_badge(db_path):
    session_id = _seed_admin_and_keys(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/settings")
        assert r.status_code == 200
        body = r.text
        # Header.
        assert "<th>Role</th>" in body
        # Both labels present.
        assert "ops-bot" in body
        assert "read-dashboard" in body
        # Admin badge is critical-styled (so operators notice the elevated key).
        # Find the ops-bot row and confirm the admin badge is there.
        ops_idx = body.index("ops-bot")
        row_chunk = body[ops_idx : ops_idx + 600]
        assert "admin" in row_chunk
        assert "badge--critical" in row_chunk
        # Viewer row has the role label too.
        view_idx = body.index("read-dashboard")
        view_chunk = body[view_idx : view_idx + 600]
        assert "viewer" in view_chunk


def test_role_default_fallback_for_legacy_rows(tmp_path):
    """The schema enforces NOT NULL on role, but the read path still defaults
    to 'admin' as belt-and-braces for older code paths that might bypass
    the constraint."""
    s = Store(str(tmp_path / "leg.db"))
    aid, _r, ah = generate_api_key()
    # Default role argument when none passed.
    s.save_api_key(aid, ah, label="legacy")
    keys = s.list_api_keys()
    assert keys[0]["role"] == "admin"
    s.close()
