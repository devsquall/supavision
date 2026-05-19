"""Redact secrets from log lines and error messages.

Logging hygiene rule for this codebase: never echo a credential value, even
inside an exception message. The Slack/Teams/PagerDuty webhook URL contains
a secret token in its path component — `https://hooks.slack.com/services/
T.../B.../<TOKEN>`. If that URL ends up in a log line, the token is now
sitting in whatever sink the log forwards to (file, syslog, Sentry,
CloudWatch). Code that logs URLs must funnel them through `redact_url` so
the path/query are masked.

The redaction is conservative (whitelist of known-sensitive hostnames):
slack, teams (office.com / webhook.office.com), pagerduty, opsgenie. For
any other host we keep the full URL — a `https://my-internal-api.example/
status` doesn't need redaction and the path is useful for debugging.

If you add a new alert backend, add the hostname suffix here. Tests in
`tests/test_log_redact.py` pin the behavior.
"""

from __future__ import annotations

from urllib.parse import urlparse

_SENSITIVE_HOST_SUFFIXES = (
    "hooks.slack.com",
    "slack.com",
    "webhook.office.com",
    "office.com",
    "events.pagerduty.com",
    "pagerduty.com",
    "api.opsgenie.com",
    "opsgenie.com",
)


def redact_url(url: str) -> str:
    """Return a logger-safe version of `url`.

    For known credential-bearing hostnames (Slack, Teams, PagerDuty, Opsgenie),
    strip the path/query/fragment — only `scheme://host[:port]/[REDACTED]`
    survives. For everything else, return the URL unchanged.

    Robust against `url` not being a real URL (the caller might pass an
    exception message that happens to be a URL) — falls back to the input
    string if parsing fails.
    """
    if not isinstance(url, str) or not url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return "[REDACTED]"
    if not parsed.scheme or not parsed.netloc:
        # Not URL-shaped — return as-is. Caller may want a plain string.
        return url
    host = parsed.hostname or ""
    if any(host == s or host.endswith("." + s) for s in _SENSITIVE_HOST_SUFFIXES):
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}/[REDACTED]"
    return url
