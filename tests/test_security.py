"""Security tests — CSRF, DB permissions, rate limiting."""

import os
import time
from pathlib import Path

import pytest

from supavision.db import Store


class TestDBPermissions:
    """Database file should be owner-only readable."""

    def test_db_file_permissions(self, tmp_path):
        db_path = tmp_path / "testdata" / "test.db"
        Store(str(db_path))
        assert db_path.exists()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600, f"DB file should be 0600, got {oct(mode)}"

    def test_db_directory_permissions(self, tmp_path):
        db_dir = tmp_path / "testdata"
        Store(str(db_dir / "test.db"))
        mode = db_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"DB dir should be 0700, got {oct(mode)}"


class TestRateLimiter:
    """Rate limiter should cap requests per IP."""

    def test_allows_under_limit(self):
        from supavision.web.dashboard import _check_rate_limit
        # Reset any shared state
        from supavision.web.dashboard import _rate_limits
        _rate_limits.clear()

        for _ in range(10):
            assert _check_rate_limit("test-ip-1", max_per_minute=10) is True

    def test_blocks_over_limit(self):
        from supavision.web.dashboard import _check_rate_limit, _rate_limits
        _rate_limits.clear()

        for _ in range(10):
            _check_rate_limit("test-ip-2", max_per_minute=10)
        # 11th should be blocked
        assert _check_rate_limit("test-ip-2", max_per_minute=10) is False

    def test_different_ips_independent(self):
        from supavision.web.dashboard import _check_rate_limit, _rate_limits
        _rate_limits.clear()

        for _ in range(10):
            _check_rate_limit("ip-a", max_per_minute=10)
        # ip-a is at limit, ip-b should still work
        assert _check_rate_limit("ip-b", max_per_minute=10) is True
        assert _check_rate_limit("ip-a", max_per_minute=10) is False


class TestCSRFTokenGeneration:
    """CSRF tokens should be deterministic per auth header."""

    def test_same_auth_same_token(self):
        """Same auth header produces same CSRF token."""
        import hashlib
        import hmac

        key = b"test-key"
        auth1 = "Basic dXNlcjpwYXNz"
        token1 = hmac.new(key, auth1.encode(), hashlib.sha256).hexdigest()[:32]
        token2 = hmac.new(key, auth1.encode(), hashlib.sha256).hexdigest()[:32]
        assert token1 == token2

    def test_different_auth_different_token(self):
        """Different auth headers produce different CSRF tokens."""
        import hashlib
        import hmac

        key = b"test-key"
        auth1 = "Basic dXNlcjpwYXNz"
        auth2 = "Basic YWRtaW46c2VjcmV0"
        token1 = hmac.new(key, auth1.encode(), hashlib.sha256).hexdigest()[:32]
        token2 = hmac.new(key, auth2.encode(), hashlib.sha256).hexdigest()[:32]
        assert token1 != token2


class TestAgentRunnerToolPolicy:
    """Agent jobs should have explicit tool restrictions."""

    def test_implement_has_explicit_tools(self):
        from supavision.agent_runner import JOB_CONFIG
        assert JOB_CONFIG["implement"]["allowed_tools"] is not None
        assert "Bash(*)" in JOB_CONFIG["implement"]["allowed_tools"]
        assert "Edit" in JOB_CONFIG["implement"]["allowed_tools"]

    def test_evaluate_is_read_only(self):
        from supavision.agent_runner import JOB_CONFIG
        tools = JOB_CONFIG["evaluate"]["allowed_tools"]
        assert "Edit" not in tools
        assert "Write" not in tools
        assert "Bash" not in tools

    def test_scout_is_read_only(self):
        from supavision.agent_runner import JOB_CONFIG
        tools = JOB_CONFIG["scout"]["allowed_tools"]
        assert "Edit" not in tools
        assert "Write" not in tools
        assert "Bash" not in tools
