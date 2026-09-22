"""API key creation and verification for machine telemetry ingestion."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import ApiKey, Host
from app.utils.time import utc_now

API_KEY_PREFIX = "tm_live_"


@dataclass(frozen=True, slots=True)
class VerifiedApiKey:
    """Identity established by a valid telemetry API key."""

    api_key_id: str
    host_id: str
    host_name: str


def _hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_api_key(db: Session, *, host: Host, name: str, created_by: str | None) -> tuple[ApiKey, str]:
    """Create a host-scoped key and return its plaintext secret exactly once."""
    secret = API_KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(
        host_id=host.id,
        name=name.strip(),
        key_prefix=secret[: len(API_KEY_PREFIX) + 8],
        key_hash=_hash_key(secret),
        created_by=created_by,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key, secret


def verify_api_key(db: Session, secret: str) -> VerifiedApiKey | None:
    """Verify an active API key and return its host identity."""
    if not secret or not secret.startswith(API_KEY_PREFIX):
        return None

    key = db.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == _hash_key(secret),
            ApiKey.revoked_at.is_(None),
        )
    )
    if key is None:
        return None

    host = db.get(Host, key.host_id)
    if host is None or not host.is_active:
        return None

    now = utc_now()
    key.last_used_at = now
    db.commit()
    return VerifiedApiKey(api_key_id=key.id, host_id=host.id, host_name=host.name)


def revoke_api_key(db: Session, key_id: str) -> bool:
    """Revoke an active API key."""
    key = db.get(ApiKey, key_id)
    if key is None or key.revoked_at is not None:
        return False
    key.revoked_at = utc_now()
    db.commit()
    return True
