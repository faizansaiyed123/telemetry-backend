"""Administrator APIs for scoped telemetry service credentials."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import Host, User
from app.models.observability import ApiKeyRecord
from app.services.api_keys import TELEMETRY_WRITE_SCOPE, generate_api_key
from app.services.audit_log import record_audit

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    host_id: str
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: [TELEMETRY_WRITE_SCOPE])

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip() for item in value if item.strip()})
        if normalized != [TELEMETRY_WRITE_SCOPE]:
            raise ValueError(f"Only the {TELEMETRY_WRITE_SCOPE} scope is currently supported")
        return normalized


class ApiKeyResponse(BaseModel):
    id: str
    host_id: str
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str


def _serialize(record: ApiKeyRecord) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=record.id,
        host_id=record.host_id,
        name=record.name,
        key_prefix=record.key_prefix,
        scopes=[scope for scope in record.scopes.split(",") if scope],
        is_active=record.is_active,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[ApiKeyResponse]:
    records = list(db.scalars(select(ApiKeyRecord).order_by(ApiKeyRecord.created_at.desc())))
    return [_serialize(record) for record in records]


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyCreateResponse:
    host = db.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    if not host.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot create a key for an inactive host")

    raw_key, key_prefix, key_hash = generate_api_key()
    record = ApiKeyRecord(
        id=str(uuid4()),
        host_id=host.id,
        name=payload.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=",".join(payload.scopes),
        is_active=True,
        created_by=current_user.id,
    )
    db.add(record)
    record_audit(
        db,
        request,
        action="api_key.created",
        actor=current_user,
        target_type="api_key",
        target_id=record.id,
        metadata={"host_id": host.id, "name": host.name, "scopes": payload.scopes},
    )
    db.commit()
    db.refresh(record)

    response = _serialize(record)
    return ApiKeyCreateResponse(**response.model_dump(), api_key=raw_key)


@router.post("/{key_id}/revoke", response_model=ApiKeyResponse)
def revoke_api_key(
    key_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyResponse:
    record = db.get(ApiKeyRecord, key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if not record.is_active:
        return _serialize(record)

    record.is_active = False
    record_audit(
        db,
        request,
        action="api_key.revoked",
        actor=current_user,
        target_type="api_key",
        target_id=record.id,
        metadata={"host_id": record.host_id, "key_prefix": record.key_prefix},
    )
    db.commit()
    db.refresh(record)
    return _serialize(record)
