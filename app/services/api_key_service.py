"""Machine credential generation and authentication helpers."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.db import ApiKey, Host
from app.utils.time import utc_now


def hash_api_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_secret() -> str:
    return "tlm_" + secrets.token_urlsafe(32)


def authenticate_api_key(
    x_telemetry_key: str | None,
    db: Session,
) -> ApiKey:
    if not x_telemetry_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telemetry API key required",
        )

    key_hash = hash_api_key(x_telemetry_key)
    key = db.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid telemetry API key",
        )

    host = db.get(Host, key.host_id)
    if host is None or not host.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telemetry host is inactive",
        )

    key.last_used_at = utc_now()
    return key


def telemetry_api_key(
    x_telemetry_key: str | None = Header(default=None, alias="X-Telemetry-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Authenticate an agent and return its host-scoped credential record."""
    return authenticate_api_key(x_telemetry_key, db)
