"""Regression tests for sidebar accessibility.

Pins three things:
1. `aria-current="page"` on the active sidebar item — screen readers announce
   "current page" when focusing the active link. Before this fix, only the
   visual `sidebar-item--active` class was used, which is invisible to
   screen reader users.
2. Sections are grouped with `role="group" aria-labelledby="<heading-id>"` so
   screen readers announce the section name ("Overview", "Resources",
   "Runs", "Settings") before listing the items inside.
3. The collapse-toggle button has both `aria-label` and `title` (title
   alone isn't reliably announced by screen readers).

Also sanity-checks the skip-link / main-content target relationship.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Session, User
from supavision.web.app import create_app
from supavision.web.auth import hash_password


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_aria.db")


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


@pytest.fixture
def client(db_path, session_id):
    app = create_app(db_path=db_path)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session_id", session_id)
        yield c


def test_dashboard_root_marks_dashboard_active(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # Dashboard link should have aria-current="page".
    m = re.search(
        r'<a\s+href="/"\s+class="sidebar-item[^"]*sidebar-item--active[^"]*"\s+aria-current="page"',
        body,
    )
    assert m, "Active dashboard link missing aria-current='page'"


def test_resources_page_marks_resources_active(client):
    r = client.get("/resources")
    assert r.status_code == 200
    body = r.text
    m = re.search(
        r'<a\s+href="/resources"\s+class="sidebar-item[^"]*sidebar-item--active[^"]*"\s+aria-current="page"',
        body,
    )
    assert m, "Active resources link missing aria-current='page'"


def test_only_one_aria_current_per_page(client):
    r = client.get("/resources")
    # aria-current="page" should appear exactly once — for the active item only.
    count = r.text.count('aria-current="page"')
    assert count == 1, f"expected exactly 1 aria-current='page', got {count}"


@pytest.mark.parametrize(
    "section_id",
    [
        "nav-section-overview",
        "nav-section-resources",
        "nav-section-runs",
        "nav-section-settings",
    ],
)
def test_each_section_has_labelled_group(client, section_id):
    r = client.get("/")
    body = r.text
    # The heading carries the id.
    assert f'id="{section_id}"' in body, f"section heading {section_id} missing"
    # And there's a role=group pointing at it.
    assert f'aria-labelledby="{section_id}"' in body, f"no group labels {section_id}"


def test_collapse_toggle_has_aria_label(client):
    r = client.get("/")
    body = r.text
    m = re.search(
        r'<button\s+class="sidebar-collapse-toggle"[^>]*aria-label="Toggle sidebar"',
        body,
    )
    assert m, "sidebar-collapse-toggle missing aria-label"


def test_skip_link_targets_existing_main(client):
    r = client.get("/")
    body = r.text
    assert 'href="#main-content"' in body, "skip link missing"
    assert 'id="main-content"' in body, "skip-link target #main-content not found"
    # And <main> should have role="main".
    assert re.search(r'<main[^>]*id="main-content"[^>]*role="main"', body)


def test_nav_landmark_has_aria_label(client):
    r = client.get("/")
    body = r.text
    assert re.search(
        r'<aside\s+class="sidebar"\s+role="navigation"\s+aria-label="Main navigation"',
        body,
    ), "sidebar landmark missing aria-label"
