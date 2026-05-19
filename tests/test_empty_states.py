"""Verify empty-state UIs branch on resources_count.

Before this fix, "/sessions", "/schedules", "/alerts", and "/activity" showed
the same empty-state copy regardless of whether the user had zero resources
(needs to add their first one) or many resources (just hasn't triggered a
run / configured a webhook / etc).

The fix: `_render` injects `resources_count` into every template context,
and each empty-state branches:
- count == 0 → "Add your first resource" CTA to /resources/new
- count >  0 → page-specific guidance + CTA to /resources
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Resource, Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_empty.db")


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


def _seed_resource(db_path: str, name: str = "prod-web") -> Resource:
    s = Store(db_path)
    r = Resource(name=name, resource_type="server")
    s.save_resource(r)
    s.close()
    return r


@pytest.mark.parametrize("path", ["/sessions", "/schedules", "/alerts", "/activity"])
def test_zero_resources_shows_add_first_resource_cta(db_path, path):
    session_id = _seed_admin(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        body = r.text
        # Every zero-resource empty state must invite the user to add one.
        assert "Add a Resource" in body, f"{path} missing 'Add a Resource' CTA"
        assert "/resources/new" in body, f"{path} missing /resources/new link"


@pytest.mark.parametrize("path", ["/sessions", "/schedules", "/alerts", "/activity"])
def test_resources_exist_but_empty_page_shows_branched_cta(db_path, path):
    session_id = _seed_admin(db_path)
    _seed_resource(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        body = r.text
        # With at least one resource, the CTA should NOT push them to add another.
        # Instead, it should point at /resources (existing list) so they can
        # trigger a run / set up alerts on the existing resource.
        assert "/resources/new" not in body or "Add a Resource" not in body, (
            f"{path} still shows 'Add a Resource' even though a resource exists"
        )
        # Should mention the existing count to set context.
        assert "1 resource" in body, f"{path} doesn't mention the resource count"


def test_resources_count_pluralizes_correctly(db_path):
    session_id = _seed_admin(db_path)
    _seed_resource(db_path, "one")
    _seed_resource(db_path, "two")
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        r = c.get("/schedules")
        assert "2 resources" in r.text, "expected '2 resources' (plural)"


def test_render_helper_exposes_count_to_all_templates(db_path):
    """Smoke check that the auto-injection in _render fires for a template
    that doesn't explicitly pass `resources_count`."""
    session_id = _seed_admin(db_path)
    # No resources seeded — count should be 0.
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        # /alerts doesn't pass resources_count in its context dict.
        r = c.get("/alerts")
        assert r.status_code == 200
        # The zero-count branch references /resources/new.
        assert "/resources/new" in r.text
