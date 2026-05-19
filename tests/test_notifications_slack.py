"""Tests for the Slack webhook resolution path after the secret-storage refactor.

Verifies:
- New path: credentials → env var → URL
- Legacy fallback: raw URL in config still works, logs a warning
- Missing env var: returns no URL, alert is skipped
- SSRF check runs at send time (URLs not validated at save time anymore)
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from supavision.models import Credential, Evaluation, Report, Resource, RunType, Severity
from supavision.notifications import _resolve_credential_url, send_alert


def _make_resource(**kwargs) -> Resource:
    return Resource(name="r", resource_type="server", **kwargs)


def _make_report() -> Report:
    return Report(resource_id="r1", run_type=RunType.HEALTH_CHECK, content="x")


def _make_eval() -> Evaluation:
    return Evaluation(
        report_id="rep1",
        resource_id="r1",
        severity=Severity.WARNING,
        summary="test",
        should_alert=True,
    )


# ── _resolve_credential_url unit tests ────────────────────────────


class TestResolveCredentialUrl:
    def test_resolves_from_credential_when_env_set(self, monkeypatch):
        r = _make_resource(credentials={"slack_webhook": Credential(env_var="MY_HOOK")})
        monkeypatch.setenv("MY_HOOK", "https://hooks.slack.com/from-cred")
        url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK", config_legacy_key="slack_webhook")
        assert url == "https://hooks.slack.com/from-cred"

    def test_falls_back_to_global_env_when_credential_unset(self, monkeypatch):
        r = _make_resource()  # no credentials
        monkeypatch.setenv("SLACK_WEBHOOK", "https://hooks.slack.com/global")
        url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK", config_legacy_key="slack_webhook")
        assert url == "https://hooks.slack.com/global"

    def test_falls_back_to_legacy_raw_config_with_warning(self, monkeypatch, caplog):
        # Neither credential nor global env is set; legacy config carries the URL.
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource(config={"slack_webhook": "https://hooks.slack.com/legacy"})
        with caplog.at_level(logging.WARNING, logger="supavision.notifications"):
            url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK", config_legacy_key="slack_webhook")
        assert url == "https://hooks.slack.com/legacy"
        assert any("raw value in config" in rec.message for rec in caplog.records)

    def test_returns_empty_when_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource()
        url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK", config_legacy_key="slack_webhook")
        assert url == ""

    def test_credential_referencing_unset_env_var_logs_warning(self, monkeypatch, caplog):
        # The credential is configured but the env var isn't set — operator visibility.
        monkeypatch.delenv("MY_HOOK", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource(credentials={"slack_webhook": Credential(env_var="MY_HOOK")})
        with caplog.at_level(logging.WARNING, logger="supavision.notifications"):
            url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK")
        assert url == ""
        assert any("MY_HOOK" in rec.message and "not set" in rec.message for rec in caplog.records)

    def test_credential_takes_precedence_over_legacy_config(self, monkeypatch):
        # Migration sanity: even if both are set, credential wins (legacy is fallback only).
        monkeypatch.setenv("MY_HOOK", "https://hooks.slack.com/cred-wins")
        r = _make_resource(
            credentials={"slack_webhook": Credential(env_var="MY_HOOK")},
            config={"slack_webhook": "https://hooks.slack.com/legacy"},
        )
        url = _resolve_credential_url(r, "slack_webhook", "SLACK_WEBHOOK", config_legacy_key="slack_webhook")
        assert url == "https://hooks.slack.com/cred-wins"


# ── send_alert integration tests ──────────────────────────────────


class TestSendAlertSlack:
    @pytest.mark.asyncio
    async def test_send_resolves_env_var_from_credentials(self, monkeypatch):
        monkeypatch.setenv("MY_HOOK", "https://hooks.slack.com/test")
        r = _make_resource(credentials={"slack_webhook": Credential(env_var="MY_HOOK")})
        with patch(
            "supavision.notifications.SlackChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_mock:
            channels, _ = await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)
        assert "slack" in channels
        send_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_unset_env_var_returns_no_url(self, monkeypatch):
        monkeypatch.delenv("MY_HOOK", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource(credentials={"slack_webhook": Credential(env_var="MY_HOOK")})
        channels, key = await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)
        assert channels == []
        assert key is None

    @pytest.mark.asyncio
    async def test_send_legacy_raw_url_in_config_still_works_and_logs_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource(config={"slack_webhook": "https://hooks.slack.com/legacy"})
        with patch(
            "supavision.notifications.SlackChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with caplog.at_level(logging.WARNING, logger="supavision.notifications"):
                channels, _ = await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)
        assert "slack" in channels
        # The deprecation warning fires every send so operators see it.
        assert any("raw value in config" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_send_blocks_ssrf_url_at_send_time(self, monkeypatch):
        # URLs are no longer validated at save time, so an SSRF-shaped URL
        # might reach send_alert via the legacy fallback. It must be rejected.
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource(config={"slack_webhook": "http://127.0.0.1:8080/hook"})
        with patch(
            "supavision.notifications.SlackChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_mock:
            channels, _ = await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)
        assert channels == []
        send_mock.assert_not_called()
