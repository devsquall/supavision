"""API key authentication for Supervisor REST API."""

from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import Depends, HTTPException, Request

from ..db import Store


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
