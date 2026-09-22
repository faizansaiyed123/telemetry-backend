"""Service-credential helpers for machine-to-machine telemetry ingestion."""

from __future__ import annotations

import hashlib
import secrets

API_KEY_PREFIX = "tm_live_"
TELEMETRY_WRITE_SCOPE = "telemetry:write"


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, visible_prefix, sha256_hash)."""
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    visible_prefix = raw_key[:16]
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, visible_prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def has_scope(scopes: str, required_scope: str) -> bool:
    return required_scope in {scope.strip() for scope in scopes.split(",") if scope.strip()}
