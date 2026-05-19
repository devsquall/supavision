"""Tests for the secrets_policy module — single source of truth for what counts as a secret."""

from __future__ import annotations

import pytest

from supavision.secrets_policy import (
    KNOWN_SECRET_KEYS,
    is_secret_key,
    is_valid_env_var_name,
    secret_keys_present,
    validate_credentials_env_vars,
    validate_no_raw_secrets,
)


class TestKnownSecretKeys:
    def test_explicit_membership(self):
        assert "aws_secret_key" in KNOWN_SECRET_KEYS
        assert "aws_access_key" in KNOWN_SECRET_KEYS
        assert "github_token" in KNOWN_SECRET_KEYS
        assert "db_password" in KNOWN_SECRET_KEYS
        assert "slack_webhook" in KNOWN_SECRET_KEYS
        # Notification-channel credentials added in 0.4.5.dev0.
        assert "webhook_url" in KNOWN_SECRET_KEYS
        assert "teams_webhook" in KNOWN_SECRET_KEYS
        assert "pagerduty_integration_key" in KNOWN_SECRET_KEYS

    def test_suffix_password(self):
        assert is_secret_key("db_password")
        assert is_secret_key("admin_password")

    def test_suffix_token(self):
        assert is_secret_key("github_token")
        assert is_secret_key("some_token")

    def test_suffix_secret(self):
        assert is_secret_key("client_secret")

    def test_suffix_api_key(self):
        assert is_secret_key("openai_api_key")

    def test_does_not_match_ssh_key_path(self):
        # ssh_key_path is a filesystem path, not key material.
        assert not is_secret_key("ssh_key_path")

    def test_csrf_token_is_allowlisted(self):
        # csrf_token matches the _token suffix rule but is a framework token,
        # not a credential. The allowlist prevents form submissions from being
        # rejected because of the CSRF field.
        assert not is_secret_key("csrf_token")

    def test_does_not_match_unrelated(self):
        assert not is_secret_key("ssh_host")
        assert not is_secret_key("ssh_user")
        assert not is_secret_key("ssh_port")
        assert not is_secret_key("db_host")
        assert not is_secret_key("db_user")
        assert not is_secret_key("notes")
        assert not is_secret_key("region")


class TestIsValidEnvVarName:
    @pytest.mark.parametrize(
        "name",
        ["FOO", "_FOO", "FOO_BAR", "AWS_SECRET_ACCESS_KEY", "X1", "_1"],
    )
    def test_valid(self, name):
        assert is_valid_env_var_name(name)

    @pytest.mark.parametrize(
        "name",
        ["", "1FOO", "foo", "Foo", "FOO BAR", "FOO-BAR", "FOO.BAR"],
    )
    def test_invalid(self, name):
        assert not is_valid_env_var_name(name)

    def test_aws_style_access_key_id_passes_shape_check(self):
        # AKIA... happens to match ^[A-Z_][A-Z0-9_]*$. The shape check is a
        # filter for obviously-wrong inputs, not a guarantee. The defense
        # against putting raw secrets in the credentials slot is layered:
        # this check rejects most cases (lowercase, punctuation), and the
        # naming convention + UI copy ("env var name") covers the rest.
        assert is_valid_env_var_name("AKIAIOSFODNN7EXAMPLE")


class TestValidateNoRawSecrets:
    def test_empty(self):
        assert validate_no_raw_secrets({}) == []
        assert validate_no_raw_secrets(None) == []

    def test_clean_config(self):
        cfg = {"ssh_host": "10.0.0.5", "ssh_user": "ops", "notes": "prod box"}
        assert validate_no_raw_secrets(cfg) == []

    def test_detects_aws_secret(self):
        offenders = validate_no_raw_secrets({"aws_secret_key": "AKIA...", "ssh_host": "x"})
        assert offenders == ["aws_secret_key"]

    def test_detects_multiple(self):
        offenders = validate_no_raw_secrets({"aws_secret_key": "x", "github_token": "y", "db_password": "z", "ok": "1"})
        assert set(offenders) == {"aws_secret_key", "github_token", "db_password"}

    def test_ignores_empty_values(self):
        # Blank form fields shouldn't trip the guard — that's a UX issue, not a leak.
        assert validate_no_raw_secrets({"aws_secret_key": ""}) == []
        assert validate_no_raw_secrets({"aws_secret_key": "   "}) == []

    def test_suffix_match(self):
        offenders = validate_no_raw_secrets({"openai_api_key": "sk-..."})
        assert offenders == ["openai_api_key"]


class TestValidateCredentialsEnvVars:
    def test_empty(self):
        assert validate_credentials_env_vars({}) == []
        assert validate_credentials_env_vars(None) == []

    def test_valid_env_var_names(self):
        creds = {"aws_secret_key": "AWS_SECRET_ACCESS_KEY", "slack_webhook": "OPS_SLACK_URL"}
        assert validate_credentials_env_vars(creds) == []

    def test_rejects_raw_secret_in_credentials_slot(self):
        creds = {"aws_secret_key": "AKIA1234567890abcdef/+"}
        # Slash and plus are not in [A-Z0-9_], so this is correctly rejected.
        assert validate_credentials_env_vars(creds) == ["aws_secret_key"]

    def test_rejects_lowercase_env_var(self):
        # By convention env vars are uppercase. Allowing lowercase makes "my secret"
        # accidentally valid; reject.
        assert validate_credentials_env_vars({"aws_secret_key": "my_secret"}) == ["aws_secret_key"]

    def test_ignores_blank(self):
        assert validate_credentials_env_vars({"aws_secret_key": ""}) == []
        assert validate_credentials_env_vars({"aws_secret_key": "   "}) == []


class TestSecretKeysPresent:
    def test_none_present(self):
        assert secret_keys_present({"ssh_host": "x"}) == []

    def test_some_present(self):
        present = secret_keys_present({"slack_webhook": "x", "ssh_host": "y", "aws_secret_key": ""})
        # Returns keys regardless of whether the value is set — used by the
        # legacy-banner UI which warns about presence, not value.
        assert set(present) == {"slack_webhook", "aws_secret_key"}
