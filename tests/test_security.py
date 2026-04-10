"""Security tests — CSRF, DB permissions, rate limiting."""

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
        from supavision.web.dashboard import _check_rate_limit, _rate_limits
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


class TestPasswordPolicy:
    """Password strength validation boundary tests."""

    def test_exactly_8_chars_passes(self):
        from supavision.web.auth import validate_password_strength
        assert validate_password_strength("abcd1234") is None

    def test_7_chars_fails(self):
        from supavision.web.auth import validate_password_strength
        result = validate_password_strength("abc1234")
        assert result is not None
        assert "8 characters" in result

    def test_common_password_rejected(self):
        from supavision.web.auth import validate_password_strength
        for pwd in ("password", "admin123", "changeme"):
            result = validate_password_strength(pwd)
            assert result is not None, f"'{pwd}' should be rejected"
            assert "common" in result.lower()

    def test_common_password_case_insensitive(self):
        from supavision.web.auth import validate_password_strength
        result = validate_password_strength("PASSWORD")
        assert result is not None
        assert "common" in result.lower()

    def test_strong_password_passes(self):
        from supavision.web.auth import validate_password_strength
        assert validate_password_strength("xK9#mNp2qR") is None


class TestSSRFProtection:
    """Webhook URL validation blocks private/internal IPs."""

    def test_blocks_private_10(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="blocked"):
            validate_webhook_url("http://10.0.0.1/hook")

    def test_blocks_private_172(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="blocked"):
            validate_webhook_url("http://172.16.0.1/hook")

    def test_blocks_private_192(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="blocked"):
            validate_webhook_url("http://192.168.1.1/hook")

    def test_blocks_loopback(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="blocked"):
            validate_webhook_url("http://127.0.0.1/hook")

    def test_blocks_link_local_aws_metadata(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="blocked"):
            validate_webhook_url("http://169.254.169.254/hook")

    def test_accepts_external_url(self):
        from unittest.mock import patch

        from supavision.notifications import validate_webhook_url

        # Mock DNS resolution to return a public IP
        fake_addrinfo = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("supavision.notifications.socket.getaddrinfo", return_value=fake_addrinfo):
            result = validate_webhook_url("https://hooks.slack.com/services/xxx")
            assert result == "https://hooks.slack.com/services/xxx"

    def test_rejects_non_http_scheme(self):
        from supavision.notifications import validate_webhook_url
        with pytest.raises(ValueError, match="http"):
            validate_webhook_url("ftp://example.com/hook")


class TestSessionTimeout:
    """Session expiry and validity checks."""

    def test_expired_session_returns_none(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        from supavision.db import Store
        from supavision.models import Session, User
        from supavision.web.auth import hash_password

        store = Store(str(tmp_path / "session_test.db"))
        user = User(email="test@example.com", password_hash=hash_password("xK9#mNp2qR"))
        store.create_user(user)

        # Create a session that expired 1 hour ago
        expired_session = Session(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        store.create_session(expired_session)

        result = store.get_session(expired_session.id)
        assert result is None
        store.close()

    def test_valid_session_returns_session(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        from supavision.db import Store
        from supavision.models import Session, User
        from supavision.web.auth import hash_password

        store = Store(str(tmp_path / "session_test.db"))
        user = User(email="test2@example.com", password_hash=hash_password("xK9#mNp2qR"))
        store.create_user(user)

        # Create a session that expires in 4 hours
        valid_session = Session(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
        store.create_session(valid_session)

        result = store.get_session(valid_session.id)
        assert result is not None
        assert result.id == valid_session.id
        assert result.user_id == user.id
        store.close()

    def test_revoked_session_returns_none(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        from supavision.db import Store
        from supavision.models import Session, User
        from supavision.web.auth import hash_password

        store = Store(str(tmp_path / "session_test.db"))
        user = User(email="test3@example.com", password_hash=hash_password("xK9#mNp2qR"))
        store.create_user(user)

        session = Session(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
        store.create_session(session)

        # Revoke the session
        store.revoke_session(session.id)

        result = store.get_session(session.id)
        assert result is None
        store.close()
