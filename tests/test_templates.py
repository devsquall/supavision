"""Tests for templates.py — loading, resolution, and security."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from supervisor.models import Credential, Resource, RunType
from supervisor.templates import (
    list_templates,
    load_template,
    resolve_credentials,
    resolve_template,
)


# ── Helper ───────────────────────────────────────────────────────


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


# ── Template loading ─────────────────────────────────────────────


class TestLoadTemplate:
    def test_load_existing_discovery_template(self):
        """Load the real server/discovery.md template."""
        content = load_template(
            "server", RunType.DISCOVERY, template_dir="templates"
        )
        assert "discovery" in content.lower() or "Discovery" in content
        assert len(content) > 100

    def test_load_existing_health_check_template(self):
        """Load the real server/health_check.md template."""
        content = load_template(
            "server", RunType.HEALTH_CHECK, template_dir="templates"
        )
        assert len(content) > 100

    def test_missing_template_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="Template not found"):
            load_template("nonexistent_type", RunType.DISCOVERY, template_dir="templates")

    def test_path_traversal_in_resource_type_blocked(self):
        """resource_type like '../secret' should be caught by path resolution check."""
        # Note: The Pydantic validator on Resource.resource_type blocks this at the model
        # level, but load_template also has its own path traversal check.
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            load_template("../secret", RunType.DISCOVERY, template_dir="templates")

    def test_path_traversal_deeper(self):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            load_template("../../etc", RunType.DISCOVERY, template_dir="templates")

    def test_load_from_custom_template_dir(self, tmp_path):
        """Create a temp template and load it."""
        tmpl_dir = tmp_path / "templates"
        (tmpl_dir / "custom_type").mkdir(parents=True)
        (tmpl_dir / "custom_type" / "discovery.md").write_text("Custom template: {{name}}")

        content = load_template("custom_type", RunType.DISCOVERY, template_dir=str(tmpl_dir))
        assert content == "Custom template: {{name}}"

    def test_load_health_check_variant(self, tmp_path):
        tmpl_dir = tmp_path / "templates"
        (tmpl_dir / "mytype").mkdir(parents=True)
        (tmpl_dir / "mytype" / "health_check.md").write_text("Health check template")

        content = load_template("mytype", RunType.HEALTH_CHECK, template_dir=str(tmpl_dir))
        assert content == "Health check template"


# ── Resource type validation ─────────────────────────────────────


class TestResourceTypeValidation:
    """The Pydantic model validator prevents path traversal characters in resource_type."""

    def test_valid_resource_type(self):
        r = Resource(name="test", resource_type="server")
        assert r.resource_type == "server"

    def test_valid_with_hyphens_underscores(self):
        r = Resource(name="test", resource_type="aws-ec2_instance")
        assert r.resource_type == "aws-ec2_instance"

    def test_rejects_dots(self):
        with pytest.raises(ValidationError):
            Resource(name="test", resource_type="../etc")

    def test_rejects_slashes(self):
        with pytest.raises(ValidationError):
            Resource(name="test", resource_type="../../etc/shadow")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            Resource(name="test", resource_type="bad type")

    def test_rejects_semicolons(self):
        with pytest.raises(ValidationError):
            Resource(name="test", resource_type="type;rm")


# ── Credential resolution ────────────────────────────────────────


class TestResolveCredentials:
    def test_resolves_from_env_var(self):
        resource = _make_resource(
            credentials={"aws_key": Credential(env_var="TEST_AWS_KEY")}
        )
        with patch.dict(os.environ, {"TEST_AWS_KEY": "AKIAEXAMPLE"}):
            resolved = resolve_credentials(resource)

        assert resolved["aws_key"] == "AKIAEXAMPLE"

    def test_missing_env_var_returns_placeholder(self):
        resource = _make_resource(
            credentials={"aws_key": Credential(env_var="NONEXISTENT_VAR_12345")}
        )
        # Ensure env var doesn't exist
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        resolved = resolve_credentials(resource)
        assert "MISSING" in resolved["aws_key"]
        assert "NONEXISTENT_VAR_12345" in resolved["aws_key"]

    def test_child_overrides_parent_credentials(self):
        child_resource = _make_resource(
            credentials={"api_key": Credential(env_var="CHILD_KEY")}
        )
        parent_creds = {"api_key": Credential(env_var="PARENT_KEY")}

        with patch.dict(os.environ, {"CHILD_KEY": "child_value", "PARENT_KEY": "parent_value"}):
            resolved = resolve_credentials(child_resource, inherited_credentials=parent_creds)

        assert resolved["api_key"] == "child_value"

    def test_inherits_parent_credentials(self):
        child_resource = _make_resource(credentials={})
        parent_creds = {"db_pass": Credential(env_var="DB_PASS_ENV")}

        with patch.dict(os.environ, {"DB_PASS_ENV": "secret123"}):
            resolved = resolve_credentials(child_resource, inherited_credentials=parent_creds)

        assert resolved["db_pass"] == "secret123"

    def test_no_credentials_returns_empty(self):
        resource = _make_resource(credentials={})
        resolved = resolve_credentials(resource)
        assert resolved == {}


# ── Template placeholder resolution ──────────────────────────────


class TestResolveTemplate:
    def test_replaces_config_placeholders(self):
        resource = _make_resource(config={"region": "us-east-1", "account_id": "123"})
        template = "Region: {{region}}, Account: {{account_id}}"
        result = resolve_template(template, resource, credentials={})
        assert result == "Region: us-east-1, Account: 123"

    def test_replaces_credential_placeholders(self):
        resource = _make_resource()
        template = "Key: {{api_key}}"
        result = resolve_template(template, resource, credentials={"api_key": "sk-12345"})
        assert result == "Key: sk-12345"

    def test_replaces_runtime_context(self):
        resource = _make_resource()
        template = "Context:\n{{system_context}}"
        runtime = {"system_context": "Server is running Ubuntu 22.04"}
        result = resolve_template(template, resource, credentials={}, runtime_context=runtime)
        assert "Ubuntu 22.04" in result

    def test_unresolved_placeholders_left_as_is(self):
        resource = _make_resource()
        template = "Value: {{unknown_placeholder}}"
        result = resolve_template(template, resource, credentials={})
        assert "{{unknown_placeholder}}" in result

    def test_resource_metadata_injected(self):
        resource = _make_resource(name="prod-server", resource_type="server")
        template = "Name: {{resource_name}}, Type: {{resource_type}}, ID: {{resource_id}}"
        result = resolve_template(template, resource, credentials={})
        assert "prod-server" in result
        assert "server" in result
        assert resource.id in result

    def test_config_overrides_resource_metadata(self):
        """Config can override default resource metadata keys."""
        resource = _make_resource(
            name="prod", config={"resource_name": "custom-name"}
        )
        template = "Name: {{resource_name}}"
        result = resolve_template(template, resource, credentials={})
        assert result == "Name: custom-name"

    def test_credentials_override_config(self):
        """Credential values layer over config values."""
        resource = _make_resource(config={"api_key": "config-value"})
        template = "Key: {{api_key}}"
        result = resolve_template(
            template, resource, credentials={"api_key": "cred-value"}
        )
        assert result == "Key: cred-value"

    def test_runtime_overrides_everything(self):
        resource = _make_resource(config={"system_context": "from-config"})
        template = "{{system_context}}"
        result = resolve_template(
            template,
            resource,
            credentials={"system_context": "from-creds"},
            runtime_context={"system_context": "from-runtime"},
        )
        assert result == "from-runtime"

    def test_whitespace_in_placeholder(self):
        """Placeholders with whitespace around key name should still resolve."""
        resource = _make_resource(config={"region": "us-west-2"})
        template = "Region: {{ region }}"
        result = resolve_template(template, resource, credentials={})
        assert result == "Region: us-west-2"

    def test_multiple_same_placeholder(self):
        resource = _make_resource(config={"name": "prod"})
        template = "{{name}} is {{name}}"
        result = resolve_template(template, resource, credentials={})
        assert result == "prod is prod"


# ── list_templates ───────────────────────────────────────────────


class TestListTemplates:
    def test_lists_real_templates(self):
        results = list_templates("templates")
        assert len(results) >= 1
        types = {r["resource_type"] for r in results}
        assert "server" in types

    def test_structure_of_results(self):
        results = list_templates("templates")
        for entry in results:
            assert "resource_type" in entry
            assert "discovery" in entry
            assert "health_check" in entry
            assert entry["discovery"] in ("yes", "no")
            assert entry["health_check"] in ("yes", "no")

    def test_server_has_both_templates(self):
        results = list_templates("templates")
        server = next(r for r in results if r["resource_type"] == "server")
        assert server["discovery"] == "yes"
        assert server["health_check"] == "yes"

    def test_missing_template_dir_returns_empty(self):
        results = list_templates("/nonexistent/path/templates")
        assert results == []

    def test_custom_template_dir(self, tmp_path):
        tmpl_dir = tmp_path / "templates"
        (tmpl_dir / "custom_type").mkdir(parents=True)
        (tmpl_dir / "custom_type" / "discovery.md").write_text("template")
        # No health_check.md

        results = list_templates(str(tmpl_dir))
        assert len(results) == 1
        assert results[0]["resource_type"] == "custom_type"
        assert results[0]["discovery"] == "yes"
        assert results[0]["health_check"] == "no"

    def test_ignores_non_directory_entries(self, tmp_path):
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        (tmpl_dir / "README.md").write_text("Not a template directory")
        (tmpl_dir / "actual_type").mkdir()
        (tmpl_dir / "actual_type" / "discovery.md").write_text("template")

        results = list_templates(str(tmpl_dir))
        assert len(results) == 1
        assert results[0]["resource_type"] == "actual_type"
