"""End-to-end write-boundary tests: every path that creates or edits a Resource
must reject raw secrets in config and require env-var references in credentials.
Also covers backward-compatibility for legacy rows that already hold raw secrets.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Credential, Resource, User
from supavision.web.auth import generate_api_key, hash_password
from supavision.web.dashboard import router as dashboard_router
from supavision.web.routes import health_router
from supavision.web.routes import router as api_router

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_secret_storage.db"
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def db_path(store) -> Path:
    return store.db_path if hasattr(store, "db_path") else Path(store._db_path)


# ── REST API ──────────────────────────────────────────────────────


@pytest.fixture
def api_app(store):
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(api_router)
    app.state.store = store
    app.state.engine = MagicMock()
    app.state.scheduler = MagicMock()
    return app


@pytest.fixture
def api_client(api_app, store):
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="test")
    return TestClient(api_app, headers={"x-api-key": raw_key})


class TestRestApiSecretStorage:
    def test_create_with_raw_secret_in_config_returns_400(self, api_client):
        r = api_client.post(
            "/api/v1/resources",
            json={
                "name": "test",
                "resource_type": "server",
                "config": {"aws_secret_key": "AKIAEXAMPLE", "ssh_host": "1.2.3.4"},
            },
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "raw_secrets_in_config"
        assert "aws_secret_key" in detail["fields"]

    def test_create_with_raw_value_in_credentials_slot_returns_400(self, api_client):
        r = api_client.post(
            "/api/v1/resources",
            json={
                "name": "test",
                "resource_type": "server",
                "config": {},
                "credentials": {"aws_secret_key": "AKIA1234/abc+def"},
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_env_var_name_in_credentials"

    def test_create_with_env_var_reference_persists_to_credentials(self, api_client, store):
        r = api_client.post(
            "/api/v1/resources",
            json={
                "name": "aws-prod",
                "resource_type": "aws_account",
                "config": {},
                "credentials": {
                    "aws_secret_key": "AWS_SECRET_ACCESS_KEY",
                    "aws_access_key": "AWS_ACCESS_KEY_ID",
                },
            },
        )
        assert r.status_code == 200
        rid = r.json()["resource_id"]
        resource = store.get_resource(rid)
        assert resource.credentials["aws_secret_key"].env_var == "AWS_SECRET_ACCESS_KEY"
        assert resource.credentials["aws_access_key"].env_var == "AWS_ACCESS_KEY_ID"
        assert "aws_secret_key" not in resource.config
        assert "aws_access_key" not in resource.config

    def test_update_with_raw_secret_in_config_returns_400(self, api_client, store):
        # First create a clean resource
        r = api_client.post(
            "/api/v1/resources",
            json={"name": "x", "resource_type": "server", "config": {"ssh_host": "1.2.3.4"}},
        )
        rid = r.json()["resource_id"]

        r2 = api_client.put(
            f"/api/v1/resources/{rid}",
            json={"config": {"github_token": "ghp_secret123"}},
        )
        assert r2.status_code == 400
        assert "github_token" in r2.json()["detail"]["fields"]

    def test_update_with_valid_credentials_persists(self, api_client, store):
        r = api_client.post(
            "/api/v1/resources",
            json={"name": "x", "resource_type": "server", "config": {"ssh_host": "1.2.3.4"}},
        )
        rid = r.json()["resource_id"]
        r2 = api_client.put(
            f"/api/v1/resources/{rid}",
            json={"credentials": {"slack_webhook": "OPS_SLACK_URL"}},
        )
        assert r2.status_code == 200
        resource = store.get_resource(rid)
        assert resource.credentials["slack_webhook"].env_var == "OPS_SLACK_URL"

    def test_db_serialized_resource_has_no_known_secret_keys_in_config(self, api_client, store, tmp_path):
        """Acceptance check from the plan: after each create path the raw DB JSON must
        not contain any KNOWN_SECRET_KEYS member as a config key."""
        from supavision.secrets_policy import KNOWN_SECRET_KEYS

        r = api_client.post(
            "/api/v1/resources",
            json={
                "name": "x",
                "resource_type": "server",
                "config": {"ssh_host": "1.2.3.4"},
                "credentials": {"slack_webhook": "OPS_SLACK_URL"},
            },
        )
        rid = r.json()["resource_id"]

        # Inspect raw DB
        store.close()
        conn = sqlite3.connect(str(store.db_path))
        row = conn.execute("SELECT data FROM resources WHERE id = ?", (rid,)).fetchone()
        conn.close()
        assert row is not None
        data = json.loads(row[0])
        for k in KNOWN_SECRET_KEYS:
            assert k not in data.get("config", {}), f"raw secret key {k!r} leaked into config"


# ── Dashboard wizard & edit ──────────────────────────────────────


def _make_dashboard_app(store: Store) -> FastAPI:
    app = FastAPI()
    admin = User(
        email="admin@test.com",
        password_hash=hash_password("Admin1234!"),
        name="Admin",
        role="admin",
    )
    store.create_user(admin)

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.csrf_token = "test-csrf"
        request.state.current_user = admin
        request.state.is_admin = True
        return await call_next(request)

    static_dir = Path(__file__).resolve().parent.parent / "src" / "supavision" / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.store = store
    app.state.engine = None
    app.state.scheduler = None
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def dash_client(store):
    return TestClient(_make_dashboard_app(store), raise_server_exceptions=False)


class TestDashboardWizardSecretStorage:
    def test_wizard_final_submit_with_env_var_reference_persists_to_credentials(self, dash_client, store):
        """The wizard's final POST should map *_env_var fields to credentials."""
        r = dash_client.post(
            "/resources/new",
            data={
                "name": "aws-prod",
                "resource_type": "aws_account",
                "aws_access_key_env_var": "AWS_ACCESS_KEY_ID",
                "aws_secret_key_env_var": "AWS_SECRET_ACCESS_KEY",
                "csrf_token": "test-csrf",
            },
            follow_redirects=False,
        )
        # Redirect to the new resource page on success.
        assert r.status_code in (302, 303), r.text
        resources = store.list_resources()
        assert len(resources) == 1
        r0 = resources[0]
        assert r0.credentials["aws_access_key"].env_var == "AWS_ACCESS_KEY_ID"
        assert r0.credentials["aws_secret_key"].env_var == "AWS_SECRET_ACCESS_KEY"
        assert "aws_secret_key" not in r0.config
        assert "aws_access_key" not in r0.config

    def test_wizard_final_submit_with_raw_aws_secret_returns_400(self, dash_client, store):
        """A tampered/stale wizard that sends raw secret keys must be rejected."""
        r = dash_client.post(
            "/resources/new",
            data={
                "name": "test",
                "resource_type": "aws_account",
                "aws_secret_key": "AKIAEXAMPLE",
                "csrf_token": "test-csrf",
            },
        )
        assert r.status_code == 400
        assert store.list_resources() == []

    def test_wizard_final_submit_with_bad_env_var_name_returns_400(self, dash_client, store):
        r = dash_client.post(
            "/resources/new",
            data={
                "name": "test",
                "resource_type": "aws_account",
                "aws_access_key_env_var": "lowercase-not-allowed",
                "aws_secret_key_env_var": "AWS_SECRET_ACCESS_KEY",
                "csrf_token": "test-csrf",
            },
        )
        assert r.status_code == 400
        assert store.list_resources() == []


class TestDashboardEditSecretStorage:
    def test_edit_post_unrelated_field_on_legacy_resource_preserves_legacy_secret(self, dash_client, store):
        """Legacy rows continue to work; editing an unrelated field doesn't strip them
        and doesn't introduce new raw secrets."""
        legacy = Resource(
            name="legacy",
            resource_type="server",
            config={"ssh_host": "1.2.3.4", "slack_webhook": "https://hooks.slack.com/old"},
        )
        store.save_resource(legacy)

        r = dash_client.post(
            f"/resources/{legacy.id}/edit",
            data={"name": "renamed", "ssh_host": "1.2.3.4", "csrf_token": "test-csrf"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.text

        reloaded = store.get_resource(legacy.id)
        assert reloaded.name == "renamed"
        # Legacy slack_webhook is preserved (not stripped, not converted).
        assert reloaded.config["slack_webhook"] == "https://hooks.slack.com/old"

    def test_edit_post_with_raw_secret_field_returns_400(self, dash_client, store):
        r0 = Resource(name="x", resource_type="server", config={"ssh_host": "1.2.3.4"})
        store.save_resource(r0)

        r = dash_client.post(
            f"/resources/{r0.id}/edit",
            data={
                "ssh_host": "1.2.3.4",
                "aws_secret_key": "AKIAINJECTED",
                "csrf_token": "test-csrf",
            },
        )
        assert r.status_code == 400
        reloaded = store.get_resource(r0.id)
        assert "aws_secret_key" not in reloaded.config


class TestSlackWebhookUpdate:
    def test_update_notifications_with_raw_url_returns_400(self, dash_client, store):
        r0 = Resource(name="x", resource_type="server")
        store.save_resource(r0)

        r = dash_client.post(
            f"/resources/{r0.id}/notifications",
            data={"slack_webhook": "https://hooks.slack.com/X", "csrf_token": "test-csrf"},
        )
        assert r.status_code == 400

    def test_update_notifications_with_env_var_persists_to_credentials(self, dash_client, store):
        r0 = Resource(name="x", resource_type="server")
        store.save_resource(r0)

        r = dash_client.post(
            f"/resources/{r0.id}/notifications",
            data={"slack_webhook_env_var": "OPS_SLACK_URL", "csrf_token": "test-csrf"},
        )
        assert r.status_code == 204
        reloaded = store.get_resource(r0.id)
        assert reloaded.credentials["slack_webhook"].env_var == "OPS_SLACK_URL"
        assert "slack_webhook" not in reloaded.config

    def test_update_notifications_with_env_var_migrates_legacy_raw_value(self, dash_client, store):
        legacy = Resource(
            name="legacy",
            resource_type="server",
            config={"slack_webhook": "https://hooks.slack.com/old"},
        )
        store.save_resource(legacy)

        r = dash_client.post(
            f"/resources/{legacy.id}/notifications",
            data={"slack_webhook_env_var": "OPS_SLACK_URL", "csrf_token": "test-csrf"},
        )
        assert r.status_code == 204
        reloaded = store.get_resource(legacy.id)
        assert reloaded.credentials["slack_webhook"].env_var == "OPS_SLACK_URL"
        # Legacy raw value is removed as part of the migration on edit.
        assert "slack_webhook" not in reloaded.config

    def test_update_notifications_with_bad_env_var_name_returns_400(self, dash_client, store):
        r0 = Resource(name="x", resource_type="server")
        store.save_resource(r0)

        r = dash_client.post(
            f"/resources/{r0.id}/notifications",
            data={"slack_webhook_env_var": "lowercase", "csrf_token": "test-csrf"},
        )
        assert r.status_code == 400


# ── CLI ───────────────────────────────────────────────────────────


def _run_cli(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the supavision CLI as a subprocess."""
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    # Force JSON output regardless of TTY detection.
    cmd = [sys.executable, "-m", "supavision.cli", "--format", "json", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


class TestCliSecretStorage:
    def test_resource_add_with_raw_secret_exits_nonzero(self, tmp_path):
        db = tmp_path / "cli.db"
        result = _run_cli(
            "--db",
            str(db),
            "resource-add",
            "x",
            "--type",
            "server",
            "--config",
            "aws_secret_key=AKIAEXAMPLE",
        )
        assert result.returncode != 0
        # Error goes to stderr and mentions both the field and the migration path.
        assert "aws_secret_key" in (result.stderr + result.stdout)
        assert "add-credential" in (result.stderr + result.stdout)

    def test_resource_add_with_clean_config_succeeds(self, tmp_path):
        db = tmp_path / "cli.db"
        result = _run_cli(
            "--db",
            str(db),
            "resource-add",
            "x",
            "--type",
            "server",
            "--config",
            "ssh_host=1.2.3.4",
        )
        assert result.returncode == 0, result.stderr

    def test_notify_configure_with_raw_slack_url_exits_nonzero(self, tmp_path):
        db = tmp_path / "cli.db"
        # Create a resource first.
        _run_cli("--db", str(db), "resource-add", "x", "--type", "server")
        # Fetch ID via resource-list (use JSON output)
        listed = _run_cli("--db", str(db), "resource-list")
        data = json.loads(listed.stdout)
        rid = data["resources"][0]["id"]

        result = _run_cli(
            "--db",
            str(db),
            "notify-configure",
            rid,
            "--slack-webhook",
            "https://hooks.slack.com/X",
        )
        assert result.returncode != 0
        assert "no longer accepted" in (result.stderr + result.stdout).lower() or "slack-webhook-env-var" in (
            result.stderr + result.stdout
        )

    def test_notify_configure_with_env_var_succeeds(self, tmp_path):
        db = tmp_path / "cli.db"
        _run_cli("--db", str(db), "resource-add", "x", "--type", "server")
        listed = _run_cli("--db", str(db), "resource-list")
        rid = json.loads(listed.stdout)["resources"][0]["id"]

        result = _run_cli(
            "--db",
            str(db),
            "notify-configure",
            rid,
            "--slack-webhook-env-var",
            "OPS_SLACK_URL",
        )
        assert result.returncode == 0, result.stderr

        # Verify the credential was persisted as an env-var reference, not a raw value.
        s = Store(db)
        reloaded = s.get_resource(rid)
        s.close()
        assert reloaded.credentials["slack_webhook"].env_var == "OPS_SLACK_URL"


# ── Legacy compatibility ─────────────────────────────────────────


class TestLegacyResourceCompatibility:
    def test_existing_resource_with_raw_secret_in_config_still_loads(self, store):
        """Round-trip a Resource whose config contains a known-secret key. The Pydantic
        model must NOT reject it (otherwise legacy rows would fail to load)."""
        legacy = Resource(
            name="legacy",
            resource_type="server",
            config={"slack_webhook": "https://hooks.slack.com/old", "aws_secret_key": "AKIA..."},
        )
        store.save_resource(legacy)

        # The load path must not raise.
        reloaded = store.get_resource(legacy.id)
        assert reloaded is not None
        assert reloaded.config["slack_webhook"] == "https://hooks.slack.com/old"
        assert reloaded.config["aws_secret_key"] == "AKIA..."

    def test_credential_object_round_trips(self, store):
        r = Resource(
            name="modern",
            resource_type="server",
            credentials={"slack_webhook": Credential(env_var="OPS_SLACK_URL")},
        )
        store.save_resource(r)
        reloaded = store.get_resource(r.id)
        assert reloaded.credentials["slack_webhook"].env_var == "OPS_SLACK_URL"
