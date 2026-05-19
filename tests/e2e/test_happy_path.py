"""Browser-driven smoke tests for the dashboard happy path.

Requires `pytest-playwright`; see ``tests/e2e/README.md``. The whole suite is
skipped unless ``SUPAVISION_E2E_BASE_URL`` is set so default ``pytest tests/``
runs are unaffected.
"""

from __future__ import annotations

import os

import pytest

playwright = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("SUPAVISION_E2E_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("SUPAVISION_E2E_EMAIL", "")
PASSWORD = os.environ.get("SUPAVISION_E2E_PASSWORD", "")


def _login(page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE_URL}/")


def test_login_then_view_dashboard(page):
    assert EMAIL and PASSWORD, "set SUPAVISION_E2E_EMAIL/PASSWORD to run"
    _login(page)
    # Dashboard renders the action queue (or the empty state if no resources).
    assert page.locator("h2:has-text('Dashboard'), h2:has-text('Action Needed')").first.is_visible()


def test_add_resource_with_env_var_credentials(page):
    """Walk the add-resource wizard end-to-end with env-var credential refs.

    Verifies that the v0.4.5 secret-storage refactor is wired into the UI:
    the credentials step accepts `<name>_env_var` field names and saves the
    resource without ever asking for raw secret values.
    """
    assert EMAIL and PASSWORD, "set SUPAVISION_E2E_EMAIL/PASSWORD to run"
    _login(page)
    page.goto(f"{BASE_URL}/resources/new?type=aws_account")

    # Step 1: resource info
    page.fill("input[name=name]", "e2e-aws-test")
    page.fill("textarea[name=notes]", "Created by e2e test")
    page.click("button:has-text('Next:')")

    # Step 2: credentials — these are env-var NAMES, not raw values.
    page.fill("input[name=aws_access_key_env_var]", "AWS_ACCESS_KEY_ID")
    page.fill("input[name=aws_secret_key_env_var]", "AWS_SECRET_ACCESS_KEY")
    page.click("button:has-text('Next:')")

    # Step 3: schedule — accept defaults
    page.click("button:has-text('Next:')")

    # Step 4: confirm
    page.click("button:has-text('Create Resource')")
    page.wait_for_url(f"{BASE_URL}/resources/**")
    assert page.locator("h2:has-text('e2e-aws-test')").is_visible()
