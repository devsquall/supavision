"""Authentication for Supavision — API keys + session-based user auth.

Two principal types share the system:

- **Session user**: a real `User` row signed in via the dashboard.
  Middleware in ``web.app`` sets ``request.state.current_user`` for these.
- **API key**: a per-key record (label, role) with no associated User.
  ``require_api_key`` (FastAPI dependency) validates the ``x-api-key`` header.

Most handlers are scoped to one mode. For the few that accept both (e.g.
``/api/v1/search``, which is reachable from both the dashboard command
palette and external API clients), use ``get_auth_context(request)`` —
it returns an ``AuthContext`` describing which principal is calling.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

from ..db import Store
from ..models import User

# ── Password hashing (scrypt — stdlib, no extra dependency) ────

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "12345678",
        "123456789",
        "qwerty123",
        "password1",
        "admin123",
        "letmein",
        "welcome",
        "changeme",
        "default",
    }
)


def hash_password(password: str) -> str:
    """Hash a password with scrypt + random salt."""
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored scrypt hash."""
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return secrets.compare_digest(key.hex(), key_hex)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> str | None:
    """Check password meets policy. Returns error message or None if valid."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password.lower() in _COMMON_PASSWORDS:
        return (
            "Password is too common. Choose something less predictable — "
            "mix uppercase, lowercase, digits, and symbols, and avoid names "
            "like 'password', 'admin', or 'supavision'."
        )
    return None


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (key_id, raw_key, key_hash)."""
    key_id = str(uuid.uuid4())
    raw_key = f"sv_{secrets.token_hex(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return key_id, raw_key, key_hash


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def get_store(request: Request) -> Store:
    return request.app.state.store


def _validate_api_key(store: Store, raw_key: str) -> dict | None:
    """Validate a raw API key string against the store. Returns the key record or None.

    Centralised so the FastAPI dependency and ``get_auth_context`` use the same
    hashing + DB-lookup path; in particular both close the "header is present"
    loophole by requiring a real DB hit.
    """
    if not raw_key:
        return None
    key_hash = hash_api_key(raw_key)
    return store.validate_api_key(key_hash)


def _resolve_session_user(store: Store, session_id: str | None) -> User | None:
    """Resolve a session cookie to a User row, or return None.

    Mirrors the session-lookup path used by the dashboard middleware so a
    dual-auth handler under ``/api/v1/*`` (which the middleware skips) can
    still validate the cookie itself.
    """
    if not session_id:
        return None
    session = store.get_session(session_id)
    if not session:
        return None
    user = store.get_user(session.user_id)
    if not user or not user.is_active:
        return None
    return user


async def require_api_key(request: Request) -> dict:
    """FastAPI dependency: validate x-api-key header."""
    key = request.headers.get("x-api-key", "")
    if not key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    store: Store = request.app.state.store
    key_record = _validate_api_key(store, key)

    if not key_record:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    return key_record


async def require_api_key_admin(request: Request) -> dict:
    """FastAPI dependency: validate API key AND require admin role."""
    key_record = await require_api_key(request)
    if key_record.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin API key required")
    return key_record


# ── Dual-auth helper (session OR api-key) ───────────────────────


@dataclass(frozen=True)
class AuthContext:
    """Resolved authentication principal for a request.

    `source="session"` populates `user` (with `api_key_id=None`);
    `source="api_key"` populates `api_key_id` (with `user=None`).
    API keys do NOT map to User rows — they are a separate principal type
    with their own role.
    """

    source: Literal["session", "api_key"]
    role: Literal["admin", "viewer"]
    user: User | None
    api_key_id: str | None


def get_auth_context(request: Request) -> AuthContext | None:
    """Return the calling principal, or None if the request is unauthenticated.

    Priority:
    1. ``x-api-key`` header (if present, *must* validate against the DB —
       header-presence alone is not enough).
    2. ``session_id`` cookie (validated directly so dual-auth handlers
       under ``/api/v1/*``, which the dashboard session middleware skips,
       still work).
    """
    store: Store = request.app.state.store

    raw_key = request.headers.get("x-api-key", "").strip()
    if raw_key:
        record = _validate_api_key(store, raw_key)
        if not record:
            # An x-api-key was supplied but did not validate — reject
            # outright. Falling through to session would let attackers bypass
            # the API-key check by sending a bogus header alongside a
            # forged/expired cookie.
            return None
        return AuthContext(
            source="api_key",
            role=record.get("role", "viewer"),
            user=None,
            api_key_id=record.get("id"),
        )

    session_id = request.cookies.get("session_id")
    user = _resolve_session_user(store, session_id)
    if user is not None:
        return AuthContext(
            source="session",
            role="admin" if user.role == "admin" else "viewer",
            user=user,
            api_key_id=None,
        )

    return None
