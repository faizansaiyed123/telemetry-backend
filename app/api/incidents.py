"""Incident correlation API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated, require_operator
from app.db.session import get_db
from app.models.db import User
from app.models.observability import IncidentResponse
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _response(incident) -> IncidentResponse:
    return IncidentResponse(
        id=incident.id,
        host_id=incident.host_id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        first_seen_at=incident.first_seen_at,
        last_seen_at=incident.last_seen_at,
        resolved_at=incident.resolved_at,
        alert_ids=sorted(incident.alert_ids),
        active_alert_count=len(incident.active_alert_ids),
    )


@router.get("", response_model=list[IncidentResponse])
def list_incidents(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    _: User = Depends(require_authenticated),
) -> list[IncidentResponse]:
    incidents = request.app.state.telemetry_manager.incident_engine.all()
    if status_filter:
        incidents = [incident for incident in incidents if incident.status == status_filter]
    return [_response(incident) for incident in incidents]


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident(
    incident_id: str,
    request: Request,
    _: User = Depends(require_authenticated),
) -> IncidentResponse:
    incident = request.app.state.telemetry_manager.incident_engine.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _response(incident)


@router.post("/{incident_id}/acknowledge", response_model=IncidentResponse)
def acknowledge_incident(
    incident_id: str,
    request: Request,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentResponse:
    engine = request.app.state.telemetry_manager.incident_engine
    incident = engine.acknowledge(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Active incident not found")

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="incident.acknowledged",
        resource_type="incident",
        resource_id=incident_id,
    )
    db.commit()
    return _response(incident)
