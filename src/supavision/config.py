"""Shared configuration for Supavision.

All configurable values in one place. Environment variables override defaults.
Safety limits (MAX_TURNS, MAX_OUTPUT_BYTES, tool allowlists) are NOT configurable.
"""

from __future__ import annotations

import os

# Backend selection is handled by engine.py (reads SUPAVISION_BACKEND env var).
# Options: claude_cli (default, zero extra cost) or openrouter (per-token API).

# OpenRouter API (only needed if SUPAVISION_BACKEND=openrouter)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# LLM model for investigation (openrouter backend only)
DEFAULT_MODEL = os.environ.get("SUPAVISION_MODEL", "anthropic/claude-sonnet-4")

# Scheduler
CHECK_INTERVAL_SECONDS = int(os.environ.get("SUPAVISION_CHECK_INTERVAL", "60"))

# Legacy auth (deprecated — use session-based auth with `supavision create-admin`)
DASHBOARD_PASSWORD = os.environ.get("SUPAVISION_PASSWORD", "")
DASHBOARD_USER = os.environ.get("SUPAVISION_USER", "admin")

# Session-based auth
SESSION_HOURS = int(os.environ.get("SUPAVISION_SESSION_HOURS", "8"))
SESSION_IDLE_MINUTES = int(os.environ.get("SUPAVISION_SESSION_IDLE_MINUTES", "120"))
SESSION_COOKIE_SECURE = os.environ.get("SUPAVISION_COOKIE_SECURE", "true").lower() != "false"

# Rate limits (per IP, per minute) — in-memory only; use reverse proxy for distributed
RATE_LIMIT_LOGIN = int(os.environ.get("SUPAVISION_RATE_LIMIT_LOGIN", "5"))
RATE_LIMIT_ASK = int(os.environ.get("SUPAVISION_RATE_LIMIT_ASK", "30"))
RATE_LIMIT_DEFAULT = int(os.environ.get("SUPAVISION_RATE_LIMIT_DEFAULT", "10"))

# Execution mode — set to "true" to enable code modification features (approve, implement)
# Default: disabled in v1 (monitoring-only mode)
EXECUTION_ENABLED = os.environ.get("SUPAVISION_EXECUTION_ENABLED", "false").lower() == "true"

# SSH multiplexing socket directory
SSH_MUX_DIR = os.environ.get("SUPAVISION_SSH_MUX_DIR", "/tmp/supavision-ssh-mux")

# Engine
CLI_TIMEOUT_SECONDS = int(os.environ.get("SUPAVISION_CLI_TIMEOUT", "180"))
