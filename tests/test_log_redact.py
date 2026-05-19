"""Tests for `_log_redact.redact_url`.

The redaction rule: webhook URLs from known-credential-bearing hostnames have
their path/query/fragment stripped so a log line cannot accidentally
exfiltrate the secret token embedded in the URL. Non-credential URLs pass
through unchanged.
"""

from __future__ import annotations

import pytest

from supavision._log_redact import redact_url


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/services/T123ABC/B456DEF/SECRETTOKEN0123456789",
        "https://hooks.slack.com/triggers/T1/B2/SECRET",
        "https://my-team.webhook.office.com/webhookb2/abc/IncomingWebhook/xxx/yyy",
        "https://webhook.office.com/webhookb2/abc/IncomingWebhook/xxx/yyy",
        "https://events.pagerduty.com/v2/enqueue",
        "https://api.opsgenie.com/v2/alerts",
    ],
)
def test_sensitive_urls_have_path_stripped(url):
    redacted = redact_url(url)
    assert "[REDACTED]" in redacted
    assert "SECRET" not in redacted
    assert "SECRETTOKEN" not in redacted
    # Host is preserved (so logs are still useful for "which channel failed").
    from urllib.parse import urlparse

    assert urlparse(url).hostname in redacted


def test_slack_hostname_match_is_suffix_safe():
    """A URL on `evil-hooks.slack.com.attacker.com` must NOT be treated as Slack."""
    url = "https://evil-hooks.slack.com.attacker.com/path/with/SECRETTOKEN"
    redacted = redact_url(url)
    # Should be unchanged — this is not actually slack.com.
    assert redacted == url


@pytest.mark.parametrize(
    "url",
    [
        "https://my-internal-api.example.com/v1/notify",
        "http://localhost:8080/webhook/incoming",
        "https://my-corp.com/alert",
    ],
)
def test_non_sensitive_urls_pass_through(url):
    assert redact_url(url) == url


def test_empty_and_none_inputs():
    assert redact_url("") == ""
    # type: ignore intentionally — defensive coverage
    assert redact_url(None) is None  # type: ignore[arg-type]


def test_malformed_input_returns_unchanged():
    # Not URL-shaped — caller passed a stray exception message.
    msg = "Connection refused: hooks.slack.com"
    assert redact_url(msg) == msg


def test_slack_subdomain_match():
    """subdomain.slack.com style URLs also get redacted."""
    url = "https://files.slack.com/api/files.upload?token=SECRET"
    redacted = redact_url(url)
    assert "SECRET" not in redacted
    assert "[REDACTED]" in redacted


def test_port_preserved_in_redacted_form():
    # Default ports (:443 on https, :80 on http) are dropped by urlparse —
    # that's standard behavior. For non-default ports, verify they survive:
    url = "http://webhook.office.com:8080/secret/path"
    redacted = redact_url(url)
    assert ":8080" in redacted
    assert "/secret/path" not in redacted
