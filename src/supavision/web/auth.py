"""Authentication for Supavision — API keys + session-based user auth."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid

from fastapi import HTTPException, Request

from ..db import Store

# ── Password hashing (scrypt — stdlib, no extra dependency) ────

_COMMON_PASSWORDS = frozenset({
    "password", "12345678", "123456789", "qwerty123", "password1",
    "admin123", "letmein", "welcome", "changeme", "default",
})


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
        return "Password is too common. Choose a stronger password."
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


async def require_api_key(request: Request) -> dict:
    """FastAPI dependency: validate x-api-key header."""
    key = request.headers.get("x-api-key", "")
    if not key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    store: Store = request.app.state.store
    key_hash = hash_api_key(key)
    key_record = store.validate_api_key(key_hash)

    if not key_record:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    return key_record


async def require_api_key_admin(request: Request) -> dict:
    """FastAPI dependency: validate API key AND require admin role."""
    key_record = await require_api_key(request)
    if key_record.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin API key required")
    return key_record
