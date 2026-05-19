"""Integration: confirm `_post_with_retry` never leaks the Slack token into logs.

Captures the logger output produced by a failing webhook POST and asserts the
secret token is NOT present anywhere in the captured records.

This is the regression guard for the redaction work — a future refactor that
removes `redact_url(url)` and goes back to logging `url` raw will trip this
test.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from supavision.notifications import _post_with_retry


@pytest.fixture
def captured_logs(caplog):
    caplog.set_level(logging.WARNING, logger="supavision.notifications")
    return caplog


@pytest.mark.asyncio
async def test_slack_token_not_in_logs_on_connection_error(captured_logs):
    secret_url = "https://hooks.slack.com/services/T_SECRET_TEAM/B_SECRET_CH/PLEASE_DO_NOT_LEAK_ME"

    async def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    # validate_webhook_url uses real DNS — bypass for the test.
    with (
        patch("supavision.notifications.validate_webhook_url"),
        patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=boom)),
    ):
        result = await _post_with_retry(secret_url, {"text": "hello"})

    assert result is False
    joined = "\n".join(r.getMessage() for r in captured_logs.records)
    assert "PLEASE_DO_NOT_LEAK_ME" not in joined, joined
    assert "T_SECRET_TEAM" not in joined, joined
    assert "B_SECRET_CH" not in joined, joined
    # But the host should be there so logs are still useful.
    assert "hooks.slack.com" in joined


@pytest.mark.asyncio
async def test_slack_token_not_in_logs_on_4xx_response(captured_logs):
    secret_url = "https://hooks.slack.com/services/T_X/B_Y/PLEASE_DO_NOT_LEAK_ME_2"

    class FakeResp:
        status_code = 403

    async def fake_post(*args, **kwargs):
        return FakeResp()

    with (
        patch("supavision.notifications.validate_webhook_url"),
        patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=fake_post)),
    ):
        await _post_with_retry(secret_url, {"text": "x"})

    joined = "\n".join(r.getMessage() for r in captured_logs.records)
    assert "PLEASE_DO_NOT_LEAK_ME_2" not in joined, joined


@pytest.mark.asyncio
async def test_non_sensitive_url_logged_in_full(captured_logs):
    """For non-credential URLs, the path is useful debugging info — keep it."""
    url = "https://internal.example.com/notify/team-alpha"

    async def boom(*args, **kwargs):
        raise httpx.ConnectError("nope")

    with (
        patch("supavision.notifications.validate_webhook_url"),
        patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=boom)),
    ):
        await _post_with_retry(url, {})

    joined = "\n".join(r.getMessage() for r in captured_logs.records)
    # Path SHOULD be visible for non-sensitive hosts.
    assert "/notify/team-alpha" in joined
