"""Incident investigation APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated, require_operator
from app.db.session import get_db
from app.models.db import AlertRecord, User
from app.models.observability import IncidentRecord
from app.services.audit_log import record_audit

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class IncidentResponse(BaseModel):
    id: str
    host_id: str | None
    title: str
    severity: str
    status: str
    started_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    active_alert_count: int
    total_alert_count: int

    model_config = {"from_attributes": True}


def _serialize(record: IncidentRecord, db: Session) -> IncidentResponse:
    total, active = db.execute(
        select(
            func.count(AlertRecord.id),
            func.count(AlertRecord.id).filter(AlertRecord.status == "active"),
        ).where(AlertRecord.incident_id == record.id)
    ).one()
    return IncidentResponse(
        id=record.id,
        host_id=record.host_id,
        title=record.title,
        severity=record.severity,
        status=record.status,
        started_at=record.started_at,
        acknowledged_at=record.acknowledged_at,
        resolved_at=record.resolved_at,
        active_alert_count=int(active or 0),
        total_alert_count=int(total or 0),
    )


@router.get("", response_model=list[IncidentResponse])
def list_incidents(
    status_value: Literal["open", "acknowledged", "resolved"] | None = Query(default=None, alias="status"),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[IncidentResponse]:
    stmt = select(IncidentRecord).order_by(IncidentRecord.started_at.desc()).limit(200)
    if status_value:
        stmt = stmt.where(IncidentRecord.status == status_value)
    records = list(db.scalars(stmt))
    return [_serialize(record, db) for record in records]


@router.post("/{incident_id}/acknowledge", response_model=IncidentResponse)
async def acknowledge_incident(
    incident_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentResponse:
    state = await request.app.state.telemetry_manager.acknowledge_incident(incident_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    record_audit(
        db,
        request,
        action="incident.acknowledged",
        actor=current_user,
        target_type="incident",
        target_id=incident_id,
    )
    db.commit()
    record = db.get(IncidentRecord, incident_id)
    return _serialize(record, db)  # type: ignore[arg-type]


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
async def resolve_incident(
    incident_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentResponse:
    state = await request.app.state.telemetry_manager.resolve_incident(incident_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    record_audit(
        db,
        request,
        action="incident.resolved",
        actor=current_user,
        target_type="incident",
        target_id=incident_id,
    )
    db.commit()
    record = db.get(IncidentRecord, incident_id)
    return _serialize(record, db)  # type: ignore[arg-type]
