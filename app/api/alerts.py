"""Alerts REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated, require_operator
from app.db.session import get_db
from app.models.alerts import Alert, AlertsResponse
from app.models.db import AlertRecord, User

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertActionResponse(BaseModel):
    status: str
    alert_id: str


def _to_alert(record: AlertRecord) -> Alert:
    return Alert(
        id=record.id,
        timestamp=record.timestamp,
        metric=record.metric,
        value=record.value,
        baseline=record.baseline,
        severity=record.severity,
        message=record.message,
        resolved=record.status == "resolved",
        resolved_at=record.resolved_at,
        acknowledged=record.acknowledged,
        host_id=record.host_id,
        source=record.source,
        rule_id=record.rule_id,
    )


@router.get("", response_model=AlertsResponse)
def get_alerts(
    request: Request,
    active_only: bool = Query(default=False, description="Filter for only active alerts"),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> AlertsResponse:
    """Return persisted alerts, newest first."""
    stmt = select(AlertRecord).order_by(AlertRecord.timestamp.desc()).limit(200)
    if active_only:
        stmt = stmt.where(AlertRecord.status == "active")

    records = list(db.scalars(stmt))
    persisted = [_to_alert(record) for record in records]
    manager = request.app.state.telemetry_manager
    live = manager.get_alerts(active_only=active_only)

    # Prefer live state for IDs that are still being updated asynchronously,
    # then include persisted history not yet present in memory.
    by_id = {alert.id: alert for alert in persisted}
    for alert in live:
        by_id[alert.id] = alert
    alerts = sorted(
        by_id.values(),
        key=lambda alert: alert.timestamp,
        reverse=True,
    )[:200]

    return AlertsResponse(
        alerts=alerts,
        active_count=sum(not alert.resolved for alert in alerts),
        total_count=len(alerts),
    )


@router.post("/{alert_id}/acknowledge", response_model=AlertActionResponse)
async def acknowledge_alert(
    alert_id: str,
    request: Request,
    _: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AlertActionResponse:
    """Mark an alert as acknowledged."""
    manager = request.app.state.telemetry_manager
    record = db.get(AlertRecord, alert_id)

    if record is None:
        if await manager.acknowledge_alert(alert_id):
            return AlertActionResponse(status="acknowledged", alert_id=alert_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    if record.acknowledged:
        return AlertActionResponse(status="already_acknowledged", alert_id=alert_id)

    record.acknowledged = True
    db.commit()
    await manager.acknowledge_alert(alert_id)
    return AlertActionResponse(status="acknowledged", alert_id=alert_id)
