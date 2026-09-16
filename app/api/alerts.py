"""Alerts REST API endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.alerts import AlertsResponse

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=AlertsResponse)
async def get_alerts(request: Request) -> AlertsResponse:
    """Get all alerts (active first, then resolved)."""
    manager = request.app.state.telemetry_manager
    alerts = manager.get_alerts()
    return AlertsResponse(
        alerts=alerts,
        active_count=manager.active_alert_count,
        total_count=manager.total_alert_count,
    )
