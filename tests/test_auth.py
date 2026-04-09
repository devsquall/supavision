"""Tests for authentication system: password hashing, sessions, roles."""
import pytest
from supavision.db import Store
from supavision.models import User, Session
from supavision.web.auth import hash_password, verify_password, validate_password_strength

class TestPasswordHashing:
    def test_hash_and_verify(self):
        h = hash_password("securepassword")
        assert verify_password("securepassword", h)

    def test_wrong_password_rejected(self):
        h = hash_password("correct")
        assert not verify_password("wrong", h)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2  # Different salts

    def test_malformed_hash_rejected(self):
        assert not verify_password("test", "not-a-valid-hash")
        assert not verify_password("test", "")

class TestPasswordPolicy:
    def test_too_short(self):
        assert validate_password_strength("short") is not None

    def test_common_password(self):
        assert validate_password_strength("password") is not None
        assert validate_password_strength("admin123") is not None

    def test_valid_password(self):
        assert validate_password_strength("myS3cureP@ss") is None

class TestUserStore:
    @pytest.fixture
    def store(self, tmp_path):
        return Store(str(tmp_path / "test.db"))

    def test_create_and_get_user(self, store):
        user = User(email="test@example.com", password_hash=hash_password("test123456"), name="Test")
        store.create_user(user)
        found = store.get_user_by_email("test@example.com")
        assert found and found.name == "Test"

    def test_duplicate_email_fails(self, store):
        user = User(email="dup@example.com", password_hash=hash_password("test123456"))
        store.create_user(user)
        with pytest.raises(Exception):  # IntegrityError
            store.create_user(User(email="dup@example.com", password_hash=hash_password("other12345")))

    def test_deactivate_user_revokes_sessions(self, store):
        user = User(email="deac@example.com", password_hash=hash_password("test123456"), role="admin")
        store.create_user(user)
        session = Session(user_id=user.id)
        store.create_session(session)
        assert store.get_session(session.id) is not None
        store.deactivate_user(user.id)
        assert store.get_session(session.id) is None  # Revoked

    def test_count_users(self, store):
        assert store.count_users() == 0
        store.create_user(User(email="a@b.com", password_hash=hash_password("test123456")))
        assert store.count_users() == 1

class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return Store(str(tmp_path / "test.db"))

    def test_create_and_get_session(self, store):
        session = Session(user_id="user-1")
        store.create_session(session)
        found = store.get_session(session.id)
        assert found and found.user_id == "user-1"

    def test_revoked_session_returns_none(self, store):
        session = Session(user_id="user-1")
        store.create_session(session)
        store.revoke_session(session.id)
        assert store.get_session(session.id) is None

    def test_revoke_user_sessions(self, store):
        s1 = Session(user_id="user-1")
        s2 = Session(user_id="user-1")
        store.create_session(s1)
        store.create_session(s2)
        store.revoke_user_sessions("user-1")
        assert store.get_session(s1.id) is None
        assert store.get_session(s2.id) is None

    def test_touch_session(self, store):
        session = Session(user_id="user-1")
        store.create_session(session)
        store.touch_session(session.id)
        found = store.get_session(session.id)
        assert found is not None  # Still valid after touch

class TestAuditLog:
    @pytest.fixture
    def store(self, tmp_path):
        return Store(str(tmp_path / "test.db"))

    def test_log_event(self, store):
        store.log_auth_event("login_success", user_id="u1", email="a@b.com", ip_address="1.2.3.4")
        log = store.get_auth_audit_log(limit=1)
        assert len(log) == 1
        assert log[0]["event"] == "login_success"
        assert log[0]["email"] == "a@b.com"
