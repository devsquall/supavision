"""Notification dispatch for Supavision alerts.

Supports Slack (Block Kit) and generic webhook channels.
Includes SSRF protection, async retry with backoff, and dedup with TTL.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import socket
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .models import Evaluation, Report, Resource

logger = logging.getLogger(__name__)

# ── SSRF Protection ─────────────────────────────────────────────

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 private
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

_WEBHOOK_TIMEOUT = 10.0
_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 3.0]
_DEDUP_MAX_SIZE = 500
_DEDUP_TTL_SECONDS = 86400  # 24 hours — persistent issues re-alert daily


def _is_blocked_ip(ip_str: str) -> bool:
    """Check if an IP address falls in any blocked range."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Invalid IP = blocked
    return any(ip in network for network in _BLOCKED_NETWORKS)


def validate_webhook_url(url: str) -> str:
    """Validate URL is not targeting internal/private networks.

    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Webhook URL must use http or https (got {parsed.scheme})")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Webhook URL has no hostname")

    # Check domain allowlist
    allowed = os.environ.get("WEBHOOK_ALLOWED_DOMAINS", "")
    if allowed:
        allowed_domains = [d.strip().lower() for d in allowed.split(",") if d.strip()]
        if hostname.lower() not in allowed_domains:
            raise ValueError(f"Hostname {hostname} not in WEBHOOK_ALLOWED_DOMAINS")

    # Resolve and check IPs
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname {hostname}: {e}")

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        if _is_blocked_ip(sockaddr[0]):
            raise ValueError(
                f"Webhook URL resolves to blocked IP range: {sockaddr[0]}"
            )

    return url


# ── Dedup with TTL ──────────────────────────────────────────────


class _DedupCache:
    """Bounded LRU set for dedup keys with TTL expiry."""

    def __init__(
        self, maxsize: int = _DEDUP_MAX_SIZE, ttl: float = _DEDUP_TTL_SECONDS
    ):
        self._cache: OrderedDict[str, float] = OrderedDict()  # key → timestamp
        self._maxsize = maxsize
        self._ttl = ttl

    def has(self, key: str) -> bool:
        if key in self._cache:
            ts = self._cache[key]
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                return True
            # Expired — remove
            del self._cache[key]
        return False

    def add(self, key: str) -> None:
        self._cache[key] = time.monotonic()
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)


_dedup = _DedupCache()


def _dedup_key(
    resource: Resource,
    evaluation: Evaluation,
    report: Report | None = None,
) -> str:
    """Build dedup key from resource + severity + top-issue/summary hash.

    Workstream A7 (R12): when the report has a structured payload, hash over
    the top issue id (stable across runs thanks to canonical slugging) for a
    more reliable dedup signal. Legacy reports and first-run scenarios still
    hash over `evaluation.summary` for backwards compatibility with existing
    in-flight dedup cache entries.
    """
    if report is not None and report.payload is not None:
        top = _select_top_issue(report.payload)
        if top is not None:
            marker = f"issue:{top.id}"
        else:
            marker = "issue:none"
    else:
        marker = "sum:" + hashlib.sha256(
            (evaluation.summary or "").encode()
        ).hexdigest()[:8]
    raw = f"{resource.id}:{evaluation.severity}:{marker}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _select_top_issue(payload):
    """Return the most severe issue in a payload, or None if empty.

    Ordering: critical > warning > info. Within the same severity, the first
    issue wins (Claude is instructed to list the most important first).
    """
    if not payload.issues:
        return None
    _rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(
        payload.issues,
        key=lambda i: (_rank.get(str(i.severity), 99),),
    )[0]


# ── Async HTTP with retry ───────────────────────────────────────


async def _post_with_retry(url: str, json_payload: dict) -> bool:
    """POST JSON with async retry on transient failures. Returns True on success.

    Performs DNS validation immediately before connection to prevent DNS rebinding attacks.
    """
    # Re-validate DNS immediately before connection to prevent DNS rebinding
    try:
        validate_webhook_url(url)
    except ValueError as e:
        logger.warning("Webhook SSRF blocked at send time: %s", e)
        return False

    async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.post(
                    url, json=json_payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code < 400:
                    return True
                if 400 <= resp.status_code < 500:
                    logger.warning(
                        "Webhook %s returned %d (permanent), not retrying",
                        url, resp.status_code,
                    )
                    return False
                # 5xx — transient, retry
                logger.warning(
                    "Webhook %s returned %d (attempt %d/%d)",
                    url, resp.status_code, attempt + 1, _MAX_RETRIES + 1,
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                logger.warning(
                    "Webhook %s failed (attempt %d/%d): %s",
                    url, attempt + 1, _MAX_RETRIES + 1, e,
                )
            except Exception as e:
                logger.warning("Webhook %s unexpected error: %s", url, e)
                return False

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])

    return False


# ── Abstract Channel ────────────────────────────────────────────


class NotificationChannel(ABC):
    @abstractmethod
    async def send(
        self, resource: Resource, report: Report, evaluation: Evaluation
    ) -> bool:
        """Send notification. Returns True on success. Never raises."""


# ── Slack Channel ───────────────────────────────────────────────

_SEVERITY_COLORS = {
    "critical": "#FF0000",
    "warning": "#FFA500",
    "healthy": "#36A64F",
}

_SEVERITY_EMOJI = {
    "critical": "\U0001f534",  # red circle
    "warning": "\U0001f7e0",  # orange circle
    "healthy": "\U0001f7e2",  # green circle
}


class SlackChannel(NotificationChannel):
    def __init__(self, webhook_url: str):
        self.webhook_url = validate_webhook_url(webhook_url)

    async def send(
        self, resource: Resource, report: Report, evaluation: Evaluation
    ) -> bool:
        try:
            payload = self._build_payload(resource, report, evaluation)
            return await _post_with_retry(self.webhook_url, payload)
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)
            return False

    def _build_payload(
        self, resource: Resource, report: Report, evaluation: Evaluation
    ) -> dict:
        """Build the Slack Block Kit payload for an alert.

        Workstream A7: when the report carries a structured payload, surface
        the top issue (title + recommendation) and the run-vs-run diff count
        inline. Legacy prose-only reports fall back to the prior truncated-
        prose format. Deep link to the report detail page is included when
        `SUPAVISION_BASE_URL` is set in the environment.
        """
        severity = str(evaluation.severity)
        color = _SEVERITY_COLORS.get(severity, "#808080")
        emoji = _SEVERITY_EMOJI.get(severity, "\u2753")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {resource.name} — {severity.upper()}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Summary:* {evaluation.summary}",
                },
            },
        ]

        if report.payload is not None:
            # A7: structured path — top issue + diff count
            top_issue = _select_top_issue(report.payload)
            if top_issue is not None:
                title_line = f"*Top issue:* {top_issue.title}"
                if top_issue.scope:
                    title_line += f"  `{top_issue.scope}`"
                issue_text = title_line
                if top_issue.recommendation:
                    issue_text += f"\n*Recommendation:* {top_issue.recommendation}"
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": issue_text},
                    }
                )

            if report.payload_diff is not None and (
                report.payload_diff.new
                or report.payload_diff.resolved
                or report.payload_diff.persisted
            ):
                diff = report.payload_diff
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    f":arrow_right: Since last run: "
                                    f"*+{len(diff.new)} new*, "
                                    f"*−{len(diff.resolved)} resolved*, "
                                    f"*={len(diff.persisted)} persisted*"
                                ),
                            }
                        ],
                    }
                )
        else:
            # Legacy fallback: truncated prose
            report_preview = self._truncate(report.content or "", 2900)
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"```{report_preview}```",
                        },
                    },
                ]
            )

        # Context footer with deep link (A7) when a base URL is configured
        base_url = os.environ.get("SUPAVISION_BASE_URL", "").rstrip("/")
        if base_url:
            footer_text = (
                f"<{base_url}/reports/{report.id}|View report> | "
                f"Type: {resource.resource_type} | {now}"
            )
        else:
            footer_text = (
                f"Report: `{report.id}` | Type: {resource.resource_type} | {now}"
            )
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": footer_text}],
            }
        )

        return {"attachments": [{"color": color, "blocks": blocks}]}

    @staticmethod
    def _truncate(text: str, limit: int = 2900) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 20] + "\n... (truncated)"


# ── Generic Webhook Channel ─────────────────────────────────────


class WebhookChannel(NotificationChannel):
    def __init__(self, webhook_url: str):
        self.webhook_url = validate_webhook_url(webhook_url)

    async def send(
        self, resource: Resource, report: Report, evaluation: Evaluation
    ) -> bool:
        try:
            payload = {
                "resource_name": resource.name,
                "resource_type": resource.resource_type,
                "resource_id": resource.id,
                "severity": str(evaluation.severity),
                "summary": evaluation.summary,
                "should_alert": evaluation.should_alert,
                "report_id": report.id,
                "report_content": (report.content or "")[:5000],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return await _post_with_retry(self.webhook_url, payload)
        except Exception as e:
            logger.warning("Webhook notification failed: %s", e)
            return False


# ── Dispatch Helper ─────────────────────────────────────────────


async def send_alert(
    resource: Resource,
    report: Report,
    evaluation: Evaluation,
    *,
    skip_dedup: bool = False,
    store=None,
) -> tuple[list[str], str | None]:
    """Dispatch alert to all configured notification channels.

    Resolution order:
    1. resource.config["slack_webhook"] -> SlackChannel
    2. os.environ["SLACK_WEBHOOK"] fallback -> SlackChannel
    3. resource.config["webhook_url"] -> WebhookChannel

    Returns (channel_names, dedup_key). Caller is responsible for
    persisting dedup_key to the resource if needed. Never raises.
    """
    # Dedup check
    key = _dedup_key(resource, evaluation, report)
    if not skip_dedup:
        # Check in-memory cache (with TTL)
        if _dedup.has(key):
            logger.debug("Alert deduped (in-memory): %s", key)
            return [], None
        # Check persisted key (for cross-restart dedup)
        last_key = resource.config.get("_last_alert_key", "")
        if last_key == key:
            logger.debug("Alert deduped (persisted): %s", key)
            return [], None

    succeeded: list[str] = []
    sent_urls: set[str] = set()

    # Slack: per-resource config
    slack_url = resource.config.get("slack_webhook", "")
    if not slack_url:
        # Fallback to env var
        slack_url = os.environ.get("SLACK_WEBHOOK", "")

    if slack_url:
        sent_urls.add(slack_url)
        channel = SlackChannel(slack_url)
        if await channel.send(resource, report, evaluation):
            succeeded.append("slack")
        else:
            logger.warning("Slack notification failed for %s", resource.name)

    # Generic webhook
    webhook_url = resource.config.get("webhook_url", "")
    if webhook_url and webhook_url not in sent_urls:
        try:
            channel = WebhookChannel(webhook_url)
            if await channel.send(resource, report, evaluation):
                succeeded.append("webhook")
            else:
                logger.warning("Webhook notification failed for %s", resource.name)
        except ValueError as e:
            logger.warning("Webhook URL rejected (SSRF): %s", e)

    # Update in-memory dedup tracking
    if succeeded and not skip_dedup:
        _dedup.add(key)

    # Log to DB if store is provided
    if store:
        try:
            for channel_name in succeeded:
                store.log_notification(
                    resource_id=resource.id,
                    channel=channel_name,
                    severity=str(evaluation.severity),
                    summary=evaluation.summary[:200],
                    status="sent",
                )
            # Log failed channels too
            all_attempted = []
            if slack_url:
                all_attempted.append("slack")
            if webhook_url:
                all_attempted.append("webhook")
            for ch in all_attempted:
                if ch not in succeeded:
                    store.log_notification(
                        resource_id=resource.id,
                        channel=ch,
                        severity=str(evaluation.severity),
                        summary=evaluation.summary[:200],
                        status="failed",
                        error="Delivery failed",
                    )
        except Exception as e:
            logger.warning("Failed to log notification: %s", e)

    return succeeded, key if succeeded else None
