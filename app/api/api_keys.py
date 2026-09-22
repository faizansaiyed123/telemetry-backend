"""Administrator API key management for telemetry agents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import ApiKey, Host, User
from app.models.observability import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from app.services.api_key_service import create_secret, hash_api_key
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _response(key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        host_id=key.host_id,
        name=key.name,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(
    host_id: str | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[ApiKeyResponse]:
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    if host_id:
        stmt = stmt.where(ApiKey.host_id == host_id)
    return [_response(key) for key in db.scalars(stmt)]


@router.post(
    "/hosts/{host_id}",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_key(
    host_id: str,
    payload: ApiKeyCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedResponse:
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    if not host.is_active:
        raise HTTPException(status_code=400, detail="Cannot create a key for an inactive host")

    secret = create_secret()
    key = ApiKey(
        host_id=host.id,
        name=payload.name,
        key_prefix=secret[:12],
        key_hash=hash_api_key(secret),
        created_by=current_user.id,
    )
    db.add(key)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=key.id,
        details={"host_id": host.id, "name": key.name},
    )
    db.commit()
    db.refresh(key)

    return ApiKeyCreatedResponse(
        **_response(key).model_dump(),
        secret=secret,
    )


@router.post("/{key_id}/revoke", response_model=ApiKeyResponse)
def revoke_api_key(
    key_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyResponse:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        from app.utils.time import utc_now
        key.revoked_at = utc_now()
        add_audit_log(
            db,
            request=request,
            actor_user_id=current_user.id,
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=key.id,
            details={"host_id": key.host_id},
        )
        db.commit()
        db.refresh(key)
    return _response(key)
