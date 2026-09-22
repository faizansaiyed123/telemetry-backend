"""Shared API authentication and authorization dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.db import User
from app.models.observability import ApiKeyRecord
from app.services.api_keys import TELEMETRY_WRITE_SCOPE, hash_api_key, has_scope
from app.utils.time import utc_now


def require_authenticated(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def require_operator(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access required",
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


@dataclass(frozen=True, slots=True)
class IngestCredential:
    api_key_id: str
    host_id: str
    scopes: frozenset[str]


def require_ingest_credential(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> IngestCredential:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telemetry API key required",
        )

    key_hash = hash_api_key(x_api_key.strip())
    record = db.scalar(
        select(ApiKeyRecord).where(
            ApiKeyRecord.key_hash == key_hash,
            ApiKeyRecord.is_active.is_(True),
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid telemetry API key",
        )
    if not has_scope(record.scopes, TELEMETRY_WRITE_SCOPE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telemetry write scope required",
        )

    record.last_used_at = utc_now()
    db.commit()
    return IngestCredential(
        api_key_id=record.id,
        host_id=record.host_id,
        scopes=frozenset(scope for scope in record.scopes.split(",") if scope),
    )
