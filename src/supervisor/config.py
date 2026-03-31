"""Shared configuration for Supervisor.

All configurable values in one place. Environment variables override defaults.
Safety limits (MAX_TURNS, MAX_OUTPUT_BYTES, tool allowlists) are NOT configurable.
"""

from __future__ import annotations

import os

# Backend selection: claude_cli (default) or openrouter
# claude_cli: Uses Claude Code CLI — covered by Claude subscription, zero extra cost
# openrouter: Uses OpenRouter API — requires OPENROUTER_API_KEY, costs per-token
BACKEND = os.environ.get("SUPERVISOR_BACKEND", "claude_cli")

# OpenRouter API (only needed if SUPERVISOR_BACKEND=openrouter)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# LLM model for investigation (openrouter backend only)
DEFAULT_MODEL = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-4")

# Scheduler
CHECK_INTERVAL_SECONDS = int(os.environ.get("SUPERVISOR_CHECK_INTERVAL", "60"))
