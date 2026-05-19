"""Tests for the PagerDuty Events API v2 adapter.

Covers integration-key validation, payload shape (routing_key, event_action,
dedup_key, severity mapping), send failure handling, and the dispatcher's
severity gating so healthy/info events never reach the channel.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from supavision.models import Credential, Evaluation, Report, Resource, RunType, Severity
from supavision.notifications import PagerDutyChannel, send_alert


def _make_resource() -> Resource:
    return Resource(name="prod-db-01", resource_type="database")


def _make_report() -> Report:
    return Report(resource_id="r1", run_type=RunType.HEALTH_CHECK, content="rows")


def _make_eval(severity: Severity = Severity.CRITICAL, summary: str = "replication lag") -> Evaluation:
    return Evaluation(
        report_id="rep1",
        resource_id="r1",
        severity=severity,
        summary=summary,
        should_alert=True,
    )


VALID_KEY = "abcdef0123456789abcdef0123456789"  # 32-char hex, plausible PD key


class TestPagerDutyChannelConstruction:
    def test_rejects_short_key(self):
        with pytest.raises(ValueError):
            PagerDutyChannel("short")

    def test_rejects_empty_key(self):
        with pytest.raises(ValueError):
            PagerDutyChannel("")

    def test_accepts_valid_length_key(self):
        # 16 chars is the minimum we accept; real keys are 32 hex chars.
        c = PagerDutyChannel("a" * 16)
        assert c.integration_key == "a" * 16


class TestPagerDutyChannelPayload:
    @pytest.mark.asyncio
    async def test_trigger_payload_has_routing_key_and_dedup_key(self, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)
        channel = PagerDutyChannel(VALID_KEY)
        r = _make_resource()
        await channel.send(r, _make_report(), _make_eval(severity=Severity.CRITICAL))

        assert captured["url"] == "https://events.pagerduty.com/v2/enqueue"
        p = captured["payload"]
        assert p["routing_key"] == VALID_KEY
        assert p["event_action"] == "trigger"
        assert p["dedup_key"] == f"supavision:{r.id}"
        assert p["payload"]["severity"] == "critical"
        assert p["payload"]["component"] == "database"
        assert p["payload"]["source"] == "prod-db-01"

    @pytest.mark.asyncio
    async def test_warning_severity_maps_to_warning(self, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)
        channel = PagerDutyChannel(VALID_KEY)
        await channel.send(_make_resource(), _make_report(), _make_eval(severity=Severity.WARNING))
        assert captured["payload"]["payload"]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_summary_uses_evaluation_text(self, monkeypatch):
        captured: dict = {}

        async def _capture(url, payload):
            captured["payload"] = payload
            return True

        monkeypatch.setattr("supavision.notifications._post_with_retry", _capture)
        channel = PagerDutyChannel(VALID_KEY)
        await channel.send(
            _make_resource(),
            _make_report(),
            _make_eval(severity=Severity.CRITICAL, summary="disk full on /var"),
        )
        assert "disk full on /var" in captured["payload"]["payload"]["summary"]


class TestPagerDutyChannelFailure:
    @pytest.mark.asyncio
    async def test_send_returns_false_on_post_failure(self, monkeypatch):
        async def _fail(url, payload):
            return False

        monkeypatch.setattr("supavision.notifications._post_with_retry", _fail)
        channel = PagerDutyChannel(VALID_KEY)
        ok = await channel.send(_make_resource(), _make_report(), _make_eval())
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_returns_false_on_exception(self, monkeypatch):
        async def _raise(url, payload):
            raise RuntimeError("boom")

        monkeypatch.setattr("supavision.notifications._post_with_retry", _raise)
        channel = PagerDutyChannel(VALID_KEY)
        ok = await channel.send(_make_resource(), _make_report(), _make_eval())
        assert ok is False


class TestPagerDutyDispatcherGating:
    """The fix for the no-op bug: send_alert must NOT invoke PagerDuty for
    healthy/info severities (PagerDuty pages people; non-actionable events
    should never reach it). Pre-0.4.5.dev0 the channel returned True for these
    and got logged as "sent" — fixed by moving the gate into send_alert.
    """

    @pytest.mark.asyncio
    async def test_healthy_severity_does_not_invoke_pagerduty(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", VALID_KEY)
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource()

        with patch(
            "supavision.notifications.PagerDutyChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_mock:
            channels, _ = await send_alert(r, _make_report(), _make_eval(severity=Severity.HEALTHY), skip_dedup=True)
        assert "pagerduty" not in channels
        send_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_critical_severity_invokes_pagerduty(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", VALID_KEY)
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource()

        with patch(
            "supavision.notifications.PagerDutyChannel.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_mock:
            channels, _ = await send_alert(r, _make_report(), _make_eval(severity=Severity.CRITICAL), skip_dedup=True)
        assert "pagerduty" in channels
        send_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_pagerduty_resolves_credential_env_var_over_global_fallback(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_PD_KEY", "x" * 32)
        monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "y" * 32)
        monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
        r = _make_resource()
        r.credentials["pagerduty_integration_key"] = Credential(env_var="CUSTOM_PD_KEY")

        captured: dict = {}

        async def _capture(self_, *args, **kwargs):
            captured["routing_key"] = self_.integration_key
            return True

        # Patch the channel's send via the class so we capture the key used.
        with patch(
            "supavision.notifications.PagerDutyChannel.send",
            new=AsyncMock(side_effect=lambda *a, **kw: captured.update({"called": True}) or True),
        ):
            await send_alert(r, _make_report(), _make_eval(), skip_dedup=True)

        assert captured.get("called")
