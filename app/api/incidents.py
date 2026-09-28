"""Incident correlation API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated, require_operator
from app.db.session import get_db
from app.models.db import AlertRecord, ChangeEvent, User
from app.models.observability import IncidentEvidenceResponse, IncidentResponse, IncidentTimelineItem, IncidentServiceImpact
from app.services.audit import add_audit_log
from app.services.incident_evidence import build_metric_findings, build_service_impacts

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _response(incident) -> IncidentResponse:
    return IncidentResponse(
        id=incident.id,
        host_id=incident.host_id,
        service_id=incident.service_id,
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



@router.get("/{incident_id}/evidence", response_model=IncidentEvidenceResponse)
def get_incident_evidence(
    incident_id: str,
    request: Request,
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> IncidentEvidenceResponse:
    """Build a deterministic incident timeline from alerts and nearby changes."""
    engine = request.app.state.telemetry_manager.incident_engine
    incident = engine.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    alert_rows = (
        list(
            db.scalars(
                select(AlertRecord).where(AlertRecord.id.in_(incident.alert_ids))
            )
        )
        if incident.alert_ids
        else []
    )

    from datetime import timedelta

    window = timedelta(minutes=30)
    changes_stmt = (
        select(ChangeEvent)
        .where(
            ChangeEvent.occurred_at >= incident.first_seen_at - window,
            ChangeEvent.occurred_at <= incident.last_seen_at + window,
        )
        .order_by(ChangeEvent.occurred_at.asc())
    )
    if incident.host_id is None:
        changes_stmt = changes_stmt.where(ChangeEvent.host_id.is_(None))
    else:
        changes_stmt = changes_stmt.where(
            (ChangeEvent.host_id == incident.host_id) | (ChangeEvent.host_id.is_(None))
        )
    change_rows = list(db.scalars(changes_stmt))

    timeline = [
        IncidentTimelineItem(
            kind="alert",
            timestamp=row.timestamp,
            title=row.message,
            severity=row.severity,
            status=row.status,
            reference_id=row.id,
            source=row.source,
        )
        for row in alert_rows
    ]
    timeline.extend(
        IncidentTimelineItem(
            kind="change",
            timestamp=row.occurred_at,
            title=row.title,
            severity=None,
            status=None,
            reference_id=row.id,
            source=row.source,
        )
        for row in change_rows
    )
    timeline.sort(key=lambda item: item.timestamp)

    metrics = {row.metric for row in alert_rows}
    highest = max((row.severity for row in alert_rows), default=incident.severity)
    findings = [
        f"{len(alert_rows)} alert(s) across {len(metrics)} metric(s); highest recorded severity: {highest}.",
        f"{len(incident.active_alert_ids)} alert(s) remain active.",
    ]
    if change_rows:
        nearest = min(
            change_rows,
            key=lambda row: abs((row.occurred_at - incident.first_seen_at).total_seconds()),
        )
        delta_minutes = round(
            (nearest.occurred_at - incident.first_seen_at).total_seconds() / 60,
            1,
        )
        relative = "after" if delta_minutes >= 0 else "before"
        findings.append(
            f"Recorded change '{nearest.title}' occurred {abs(delta_minutes)} minute(s) {relative} "
            "the first incident signal."
        )
    else:
        findings.append("No recorded change event was found in the ±30 minute correlation window.")

    metric_findings = build_metric_findings(
        db,
        alert_ids=set(incident.alert_ids),
        host_id=incident.host_id,
        first_seen_at=incident.first_seen_at,
        last_seen_at=incident.last_seen_at,
    )
    service_impacts = [
        IncidentServiceImpact(**impact)
        for impact in build_service_impacts(db, service_id=incident.service_id)
    ]

    return IncidentEvidenceResponse(
        incident=_response(incident),
        timeline=timeline,
        alert_count=len(alert_rows),
        metric_count=len(metrics),
        change_count=len(change_rows),
        correlation_window_minutes=30,
        findings=findings,
        metric_findings=metric_findings,
        service_impacts=service_impacts,
    )


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
