"""Shared configuration for Supervisor.

All configurable values in one place. Environment variables override defaults.
Safety limits (MAX_TURNS, MAX_OUTPUT_BYTES, tool allowlists) are NOT configurable.
"""

from __future__ import annotations

import os

# OpenRouter API
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# LLM models
DEFAULT_MODEL = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-4")
EVAL_MODEL = os.environ.get("SUPERVISOR_EVAL_MODEL", "anthropic/claude-3.5-haiku")

# Scheduler
CHECK_INTERVAL_SECONDS = int(os.environ.get("SUPERVISOR_CHECK_INTERVAL", "60"))
