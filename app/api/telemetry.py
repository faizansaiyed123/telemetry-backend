"""Telemetry REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.models.telemetry import CurrentTelemetryResponse, HistoryResponse, TelemetryStats

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("/current", response_model=CurrentTelemetryResponse)
async def get_current_telemetry(request: Request) -> CurrentTelemetryResponse:
    """Get the most recent telemetry event."""
    manager = request.app.state.telemetry_manager
    event = manager.get_current()
    return CurrentTelemetryResponse(
        event=event,
        available=event is not None,
    )


@router.get("/history", response_model=HistoryResponse)
async def get_telemetry_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=5000, description="Number of events to return"),
) -> HistoryResponse:
    """Get telemetry history (most recent `limit` events in chronological order)."""
    manager = request.app.state.telemetry_manager
    events = manager.get_history(limit=limit)
    return HistoryResponse(
        events=events,
        count=len(events),
        limit=limit,
    )


@router.get("/stats", response_model=TelemetryStats)
async def get_telemetry_stats(request: Request) -> TelemetryStats:
    """Get aggregated statistics over the telemetry history."""
    manager = request.app.state.telemetry_manager
    return manager.get_stats()
