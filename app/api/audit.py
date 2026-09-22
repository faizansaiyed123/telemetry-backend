"""Administrator audit-log query API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import User
from app.models.observability import AuditLogRecord

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


@router.get("", response_model=list[AuditLogResponse])
def list_audit_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLogResponse]:
    records = list(
        db.scalars(select(AuditLogRecord).order_by(AuditLogRecord.created_at.desc()).limit(limit))
    )
    return [
        AuditLogResponse(
            id=record.id,
            actor_user_id=record.actor_user_id,
            action=record.action,
            target_type=record.target_type,
            target_id=record.target_id,
            metadata=record.metadata_json,
            ip_address=record.ip_address,
            user_agent=record.user_agent,
            created_at=record.created_at,
        )
        for record in records
    ]
