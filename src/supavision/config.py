"""Shared configuration for Supavision.

All configurable values in one place. Environment variables override defaults.
Safety limits (MAX_TURNS, MAX_OUTPUT_BYTES, tool allowlists) are NOT configurable.
"""

from __future__ import annotations

import os

# Backend selection: claude_cli (default) or openrouter
# claude_cli: Uses Claude Code CLI — covered by Claude subscription, zero extra cost
# openrouter: Uses OpenRouter API — requires OPENROUTER_API_KEY, costs per-token
BACKEND = os.environ.get("SUPAVISION_BACKEND", "claude_cli")

# OpenRouter API (only needed if SUPAVISION_BACKEND=openrouter)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# LLM model for investigation (openrouter backend only)
DEFAULT_MODEL = os.environ.get("SUPAVISION_MODEL", "anthropic/claude-sonnet-4")

# Scheduler
CHECK_INTERVAL_SECONDS = int(os.environ.get("SUPAVISION_CHECK_INTERVAL", "60"))

# Dashboard authentication
# Legacy basic auth (deprecated — use session-based auth with create-admin CLI)
DASHBOARD_PASSWORD = os.environ.get("SUPAVISION_PASSWORD", "")
DASHBOARD_USER = os.environ.get("SUPAVISION_USER", "admin")

# Session-based auth
SESSION_HOURS = int(os.environ.get("SUPAVISION_SESSION_HOURS", "8"))
SESSION_IDLE_MINUTES = int(os.environ.get("SUPAVISION_SESSION_IDLE_MINUTES", "120"))
SESSION_COOKIE_SECURE = os.environ.get("SUPAVISION_COOKIE_SECURE", "true").lower() != "false"

# Execution mode — set to "true" to enable code modification features (approve, implement)
# Default: disabled in v1 (monitoring-only mode)
EXECUTION_ENABLED = os.environ.get("SUPAVISION_EXECUTION_ENABLED", "false").lower() == "true"

# Engine
CLI_TIMEOUT_SECONDS = int(os.environ.get("SUPAVISION_CLI_TIMEOUT", "180"))
