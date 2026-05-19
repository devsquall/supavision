"""Tests for the Microsoft Teams notification adapter.

Covers payload shape, severity → themeColor mapping, send failure, and
SSRF protection at construction time. Patches `_post_with_retry` so no
actual HTTP requests fire.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from supavision.models import Evaluation, Report, Resource, RunType, Severity
from supavision.notifications import TeamsChannel


def _make_resource() -> Resource:
    return Resource(name="prod-web-01", resource_type="server")


def _make_report() -> Report:
    return Report(resource_id="r1", run_type=RunType.HEALTH_CHECK, content="x")


def _make_eval(severity: Severity = Severity.WARNING, summary: str = "disk low") -> Evaluation:
    return Evaluation(
        report_id="rep1",
        resource_id="r1",
        severity=severity,
        summary=summary,
        should_alert=True,
    )


class TestTeamsChannelConstruction:
    def test_rejects_ssrf_url(self):
        # validate_webhook_url blocks 127.0.0.1
        with pytest.raises(ValueError):
            TeamsChannel("http://127.0.0.1/hook")

    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError):
            TeamsChannel("file:///etc/passwd")


class TestTeamsChannelSeverityColor:
    @pytest.mark.parametrize(
        "severity,expected",
        [
            (Severity.CRITICAL, "D1242F"),
            (Severity.WARNING, "BF8700"),
            (Severity.HEALTHY, "1A7F37"),
        ],
    )
    @pytest.mark.asyncio
    async def test_theme_color_matches_severity(self, severity, expected, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)

        # Stub validate_webhook_url so the public-resolver SSRF check passes
        # for a fake URL we're not actually hitting.
        monkeypatch.setattr("supavision.notifications.validate_webhook_url", lambda u: u)

        channel = TeamsChannel("https://outlook.office.com/webhook/test")
        ok = await channel.send(_make_resource(), _make_report(), _make_eval(severity=severity))
        assert ok is True
        assert captured["payload"]["themeColor"] == expected

    @pytest.mark.asyncio
    async def test_unknown_severity_falls_back_to_info_color(self, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)
        monkeypatch.setattr("supavision.notifications.validate_webhook_url", lambda u: u)

        channel = TeamsChannel("https://outlook.office.com/webhook/test")
        ev = _make_eval()
        # Bypass the enum validation by mocking the str cast.
        with patch("supavision.notifications.str", side_effect=lambda x: "unknown" if x is ev.severity else str(x)):
            await channel.send(_make_resource(), _make_report(), ev)
        assert captured["payload"]["themeColor"] == "0969DA"


class TestTeamsChannelPayloadShape:
    @pytest.mark.asyncio
    async def test_payload_is_message_card_with_expected_facts(self, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)
        monkeypatch.setattr("supavision.notifications.validate_webhook_url", lambda u: u)

        channel = TeamsChannel("https://outlook.office.com/webhook/test")
        await channel.send(_make_resource(), _make_report(), _make_eval(summary="cpu spike"))

        p = captured["payload"]
        assert p["@type"] == "MessageCard"
        assert p["@context"] == "https://schema.org/extensions"
        assert "prod-web-01" in p["title"]
        assert "WARNING" in p["title"]
        assert p["text"] == "cpu spike"
        facts = {f["name"]: f["value"] for f in p["sections"][0]["facts"]}
        assert facts["Resource type"] == "server"
        assert facts["Report"]  # has a value
        assert facts["Resource ID"]


class TestTeamsChannelFailureModes:
    @pytest.mark.asyncio
    async def test_send_returns_false_on_post_failure(self, monkeypatch):
        async def _fail(url, payload):
            return False

        monkeypatch.setattr("supavision.notifications._post_with_retry", _fail)
        monkeypatch.setattr("supavision.notifications.validate_webhook_url", lambda u: u)

        channel = TeamsChannel("https://outlook.office.com/webhook/test")
        ok = await channel.send(_make_resource(), _make_report(), _make_eval())
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_returns_false_on_exception(self, monkeypatch):
        async def _raise(url, payload):
            raise RuntimeError("boom")

        monkeypatch.setattr("supavision.notifications._post_with_retry", _raise)
        monkeypatch.setattr("supavision.notifications.validate_webhook_url", lambda u: u)

        channel = TeamsChannel("https://outlook.office.com/webhook/test")
        ok = await channel.send(_make_resource(), _make_report(), _make_eval())
        assert ok is False


class TestTeamsViaSendAlert:
    """End-to-end: send_alert resolves teams_webhook via credentials and dispatches."""

    @pytest.mark.asyncio
    async def test_send_alert_dispatches_teams_when_env_set(self, monkeypatch):
        from supavision.models import Credential
        from supavision.notifications import send_alert

        monkeypatch.setenv("MY_TEAMS_HOOK", "https://outlook.office.com/webhook/test")
        # SLACK_WEBHOOK fallback must NOT be set or Slack would also fire.
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)

        r = _make_resource()
        r.credentials["teams_webhook"] = Credential(env_var="MY_TEAMS_HOOK")

        with patch(
            "supavision.notifications.TeamsChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_mock:
            with patch("supavision.notifications.validate_webhook_url", lambda u: u):
                channels, _ = await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)
        assert "teams" in channels
        send_mock.assert_called_once()
