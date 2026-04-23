"""Fast, subprocess-free Claude CLI authentication detection.

Shared by cli.py (doctor/setup commands) and web/app.py (startup warning).
Stdlib-only — no FastAPI or SQLite imports.

Limitation: checks that credentials *exist*, not that they are currently valid.
An expired or revoked OAuth token will pass here but fail at run time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def check_claude_auth() -> tuple[bool, str]:
    """Return (authenticated, human-readable detail).

    Detection order:
    1. ANTHROPIC_API_KEY env var (API key auth)
    2. ~/.claude/.credentials.json with 'claudeAiOauth' key (OAuth)
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "authenticated (ANTHROPIC_API_KEY)"

    try:
        creds_file = Path.home() / ".claude" / ".credentials.json"
    except RuntimeError:
        # Path.home() can fail in unusual environments (no HOME, no passwd entry)
        return False, "not authenticated — run 'claude login'"

    if creds_file.exists():
        try:
            data = json.loads(creds_file.read_text())
            if data.get("claudeAiOauth"):
                return True, "authenticated (OAuth)"
        except (json.JSONDecodeError, OSError):
            pass

    return False, "not authenticated — run 'supavision setup' or 'claude login'"
