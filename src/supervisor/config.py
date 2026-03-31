"""Shared configuration for Supervisor.

All configurable values in one place. Environment variables override defaults.
Safety limits (MAX_TURNS, MAX_OUTPUT_BYTES, tool allowlists) are NOT configurable.
"""

from __future__ import annotations

import os

# OpenRouter API (used by engine for agentic tool_use loop)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# LLM model for investigation (discovery + health checks)
DEFAULT_MODEL = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-4")

# Scheduler
CHECK_INTERVAL_SECONDS = int(os.environ.get("SUPERVISOR_CHECK_INTERVAL", "60"))
