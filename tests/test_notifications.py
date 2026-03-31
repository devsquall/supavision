"""Tests for the notifications module."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from supervisor.discovery_diff import ContextDiff, SectionDiff, compute_diff, format_drift_summary, should_alert_on_drift
from supervisor.models import Evaluation, Report, Resource, RunType, Severity
from supervisor.notifications import (
    SlackChannel,
    WebhookChannel,
    _DedupCache,
    _dedup_key,
    _is_blocked_ip,
    send_alert,
    validate_webhook_url,
)


# ── SSRF Protection ─────────────────────────────────────────────


class TestSSRF:
    def test_blocks_loopback(self):
        assert _is_blocked_ip("127.0.0.1") is True
        assert _is_blocked_ip("127.0.0.2") is True

    def test_blocks_private_10(self):
        assert _is_blocked_ip("10.0.0.1") is True
        assert _is_blocked_ip("10.255.255.255") is True

    def test_blocks_private_172(self):
        assert _is_blocked_ip("172.16.0.1") is True
        assert _is_blocked_ip("172.31.255.255") is True

    def test_blocks_private_192(self):
        assert _is_blocked_ip("192.168.0.1") is True
        assert _is_blocked_ip("192.168.255.255") is True

    def test_blocks_aws_metadata(self):
        assert _is_blocked_ip("169.254.169.254") is True

    def test_blocks_link_local(self):
        assert _is_blocked_ip("169.254.0.1") is True

    def test_blocks_ipv6_loopback(self):
        assert _is_blocked_ip("::1") is True

    def test_allows_public_ip(self):
        assert _is_blocked_ip("8.8.8.8") is False
        assert _is_blocked_ip("1.1.1.1") is False

    def test_blocks_invalid_ip(self):
        assert _is_blocked_ip("not-an-ip") is True

    def test_validate_rejects_private_ip(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked IP range"):
                validate_webhook_url("https://internal.example.com/hook")

    def test_validate_allows_public_ip(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("104.18.0.1", 443)),
        ]):
            result = validate_webhook_url("https://hooks.slack.com/services/xxx")
            assert result == "https://hooks.slack.com/services/xxx"

    def test_validate_rejects_bad_scheme(self):
        with pytest.raises(ValueError, match="http or https"):
            validate_webhook_url("ftp://example.com/hook")

    def test_validate_rejects_no_hostname(self):
        with pytest.raises(ValueError, match="no hostname"):
            validate_webhook_url("https:///path")

    def test_validate_domain_allowlist(self):
        with patch.dict(os.environ, {"WEBHOOK_ALLOWED_DOMAINS": "hooks.slack.com,example.com"}):
            with pytest.raises(ValueError, match="not in WEBHOOK_ALLOWED_DOMAINS"):
                validate_webhook_url("https://evil.com/hook")

    def test_validate_domain_allowlist_allows(self):
        with patch.dict(os.environ, {"WEBHOOK_ALLOWED_DOMAINS": "hooks.slack.com"}):
            with patch("socket.getaddrinfo", return_value=[
                (2, 1, 6, "", ("104.18.0.1", 443)),
            ]):
                result = validate_webhook_url("https://hooks.slack.com/services/xxx")
                assert "hooks.slack.com" in result


# ── Helpers ─────────────────────────────────────────────────────


def _make_resource(**kwargs) -> Resource:
    defaults = {"name": "test-server", "resource_type": "server"}
    defaults.update(kwargs)
    return Resource(**defaults)


def _make_report(**kwargs) -> Report:
    defaults = {"resource_id": "res-1", "run_type": RunType.HEALTH_CHECK, "content": "Test report content"}
    defaults.update(kwargs)
    return Report(**defaults)


def _make_eval(**kwargs) -> Evaluation:
    defaults = {
        "report_id": "rpt-1",
        "resource_id": "res-1",
        "severity": Severity.WARNING,
        "summary": "Test summary",
        "should_alert": True,
    }
    defaults.update(kwargs)
    return Evaluation(**defaults)


# ── Slack Channel ───────────────────────────────────────────────


class TestSlackChannel:
    def test_build_payload_structure(self):
        channel = SlackChannel("https://hooks.slack.com/test")
        resource = _make_resource()
        report = _make_report()
        evaluation = _make_eval(severity=Severity.CRITICAL)

        payload = channel._build_payload(resource, report, evaluation)

        assert "attachments" in payload
        att = payload["attachments"][0]
        assert att["color"] == "#FF0000"  # critical = red
        assert len(att["blocks"]) == 5  # header, summary, divider, report, context

    def test_build_payload_warning_color(self):
        channel = SlackChannel("https://hooks.slack.com/test")
        payload = channel._build_payload(_make_resource(), _make_report(), _make_eval(severity=Severity.WARNING))
        assert payload["attachments"][0]["color"] == "#FFA500"

    def test_build_payload_healthy_color(self):
        channel = SlackChannel("https://hooks.slack.com/test")
        payload = channel._build_payload(_make_resource(), _make_report(), _make_eval(severity=Severity.HEALTHY))
        assert payload["attachments"][0]["color"] == "#36A64F"

    def test_truncation(self):
        assert SlackChannel._truncate("short") == "short"
        long_text = "x" * 3000
        truncated = SlackChannel._truncate(long_text, 2900)
        assert len(truncated) <= 2900
        assert "truncated" in truncated

    @pytest.mark.asyncio
    async def test_send_success(self):
        channel = SlackChannel("https://hooks.slack.com/test")
        with patch("supervisor.notifications._post_with_retry", new_callable=AsyncMock, return_value=True):
            assert await channel.send(_make_resource(), _make_report(), _make_eval()) is True

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self):
        channel = SlackChannel("https://hooks.slack.com/test")
        with patch("supervisor.notifications._post_with_retry", new_callable=AsyncMock, return_value=False):
            assert await channel.send(_make_resource(), _make_report(), _make_eval()) is False


# ── Webhook Channel ─────────────────────────────────────────────


class TestWebhookChannel:
    def test_ssrf_validated_on_construction(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked IP"):
                WebhookChannel("https://internal.example.com/hook")

    @pytest.mark.asyncio
    async def test_send_payload_structure(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("104.18.0.1", 443)),
        ]):
            channel = WebhookChannel("https://example.com/hook")

        with patch("supervisor.notifications._post_with_retry", new_callable=AsyncMock, return_value=True) as mock_post:
            result = await channel.send(_make_resource(), _make_report(), _make_eval())
            assert result is True
            payload = mock_post.call_args[0][1]
            assert "resource_name" in payload
            assert "severity" in payload
            assert "timestamp" in payload


# ── Dedup ────────────────────────────────────────────────────────


class TestDedup:
    def test_dedup_cache_basic(self):
        cache = _DedupCache(maxsize=3, ttl=3600)
        assert cache.has("a") is False
        cache.add("a")
        assert cache.has("a") is True

    def test_dedup_cache_eviction(self):
        cache = _DedupCache(maxsize=2, ttl=3600)
        cache.add("a")
        cache.add("b")
        cache.add("c")  # evicts "a"
        assert cache.has("a") is False
        assert cache.has("b") is True
        assert cache.has("c") is True

    def test_dedup_cache_ttl_expiry(self):
        cache = _DedupCache(maxsize=10, ttl=0.1)  # 100ms TTL
        cache.add("a")
        assert cache.has("a") is True
        time.sleep(0.15)
        assert cache.has("a") is False  # expired

    def test_dedup_key_deterministic(self):
        r = _make_resource()
        e = _make_eval()
        key1 = _dedup_key(r, e)
        key2 = _dedup_key(r, e)
        assert key1 == key2

    def test_dedup_key_differs_by_severity(self):
        r = _make_resource()
        e1 = _make_eval(severity=Severity.WARNING)
        e2 = _make_eval(severity=Severity.CRITICAL)
        assert _dedup_key(r, e1) != _dedup_key(r, e2)

    def test_dedup_key_differs_by_summary(self):
        r = _make_resource()
        e1 = _make_eval(summary="Disk full")
        e2 = _make_eval(summary="Memory pressure")
        assert _dedup_key(r, e1) != _dedup_key(r, e2)

    def test_dedup_key_same_across_different_reports(self):
        """Same resource + severity + summary = same key, even with different report_ids."""
        r = _make_resource()
        e1 = _make_eval(report_id="report-1", summary="Disk full")
        e2 = _make_eval(report_id="report-2", summary="Disk full")
        assert _dedup_key(r, e1) == _dedup_key(r, e2)


# ── send_alert dispatch ─────────────────────────────────────────


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_no_config_returns_empty(self):
        resource = _make_resource(config={})
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLACK_WEBHOOK", None)
            channels, key = await send_alert(resource, _make_report(), _make_eval())
            assert channels == []
            assert key is None

    @pytest.mark.asyncio
    async def test_slack_from_env_fallback(self):
        resource = _make_resource(config={})
        with patch.dict(os.environ, {"SLACK_WEBHOOK": "https://hooks.slack.com/test"}):
            with patch("supervisor.notifications.SlackChannel.send", new_callable=AsyncMock, return_value=True):
                channels, key = await send_alert(resource, _make_report(), _make_eval(), skip_dedup=True)
                assert "slack" in channels

    @pytest.mark.asyncio
    async def test_slack_from_resource_config(self):
        resource = _make_resource(config={"slack_webhook": "https://hooks.slack.com/resource"})
        with patch("supervisor.notifications.SlackChannel.send", new_callable=AsyncMock, return_value=True):
            channels, key = await send_alert(resource, _make_report(), _make_eval(), skip_dedup=True)
            assert "slack" in channels

    @pytest.mark.asyncio
    async def test_dedup_prevents_second_send(self):
        resource = _make_resource(config={"slack_webhook": "https://hooks.slack.com/test"})
        report = _make_report()
        evaluation = _make_eval()

        with patch("supervisor.notifications.SlackChannel.send", new_callable=AsyncMock, return_value=True):
            channels1, key1 = await send_alert(resource, report, evaluation)
            assert "slack" in channels1
            assert key1 is not None

            channels2, key2 = await send_alert(resource, report, evaluation)
            assert channels2 == []  # deduped
            assert key2 is None

    @pytest.mark.asyncio
    async def test_skip_dedup_always_sends(self):
        resource = _make_resource(config={"slack_webhook": "https://hooks.slack.com/test"})
        report = _make_report()
        evaluation = _make_eval()

        with patch("supervisor.notifications.SlackChannel.send", new_callable=AsyncMock, return_value=True):
            await send_alert(resource, report, evaluation, skip_dedup=True)
            channels, _ = await send_alert(resource, report, evaluation, skip_dedup=True)
            assert "slack" in channels

    @pytest.mark.asyncio
    async def test_returns_dedup_key_for_persistence(self):
        resource = _make_resource(config={"slack_webhook": "https://hooks.slack.com/test"})
        evaluation = _make_eval(summary="Disk critical")

        with patch("supervisor.notifications.SlackChannel.send", new_callable=AsyncMock, return_value=True):
            channels, key = await send_alert(resource, _make_report(), evaluation, skip_dedup=True)
            assert key is not None
            assert len(key) == 16  # sha256[:16]


# ── Discovery Diff ──────────────────────────────────────────────


class TestDiscoveryDiff:
    def test_identical_content(self):
        content = "## Services\n- nginx running\n\n## Disk\n- 50% used"
        diff = compute_diff(content, content)
        assert diff.has_changes is False
        assert diff.is_significant is False

    def test_added_section(self):
        old = "## Services\n- nginx running"
        new = "## Services\n- nginx running\n\n## Monitoring\n- prometheus installed"
        diff = compute_diff(new, old)
        assert diff.total_added == 1
        assert diff.is_significant is True
        assert any(s.heading == "Monitoring" and s.change_type == "added" for s in diff.sections)

    def test_removed_section(self):
        old = "## Services\n- nginx\n\n## Legacy\n- old stuff"
        new = "## Services\n- nginx"
        diff = compute_diff(new, old)
        assert diff.total_removed == 1
        assert diff.is_significant is True

    def test_changed_section(self):
        old = "## Disk\n- 50% used"
        new = "## Disk\n- 97% used"
        diff = compute_diff(new, old)
        assert diff.total_changed == 1
        assert diff.is_significant is False  # single change = not significant

    def test_multiple_changes_significant(self):
        old = "## Disk\n- 50%\n\n## Memory\n- 4GB"
        new = "## Disk\n- 97%\n\n## Memory\n- 8GB"
        diff = compute_diff(new, old)
        assert diff.total_changed == 2
        assert diff.is_significant is True

    def test_format_drift_summary(self):
        old = "## Services\n- nginx"
        new = "## Services\n- nginx\n- redis\n\n## Monitoring\n- new"
        diff = compute_diff(new, old)
        summary = format_drift_summary(diff, "prod-server")
        assert "prod-server" in summary
        assert "Added" in summary
        assert "Monitoring" in summary

    def test_should_alert_on_drift(self):
        diff = ContextDiff(total_added=1)
        diff.sections = [SectionDiff(heading="New", change_type="added")]
        assert should_alert_on_drift(diff) is True

        diff2 = ContextDiff(total_changed=1)
        diff2.sections = [SectionDiff(heading="Disk", change_type="changed")]
        assert should_alert_on_drift(diff2) is False
