"""Telemetry REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated
from app.db.session import get_db
from app.models.db import TelemetryRecord, User
from app.models.telemetry import CurrentTelemetryResponse, HistoryResponse, TelemetryEvent, TelemetryStats
from app.services.aggregation import compute_stats

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


def _to_event(record: TelemetryRecord) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=record.timestamp,
        sequence=record.sequence,
        cpu=record.cpu,
        memory=record.memory,
        temperature=record.temperature,
        network_mbps=record.network_mbps,
        requests_per_second=record.requests_per_second,
        error_rate=record.error_rate,
        latency_ms=record.latency_ms,
    )


@router.get("/current", response_model=CurrentTelemetryResponse)
async def get_current_telemetry(
    request: Request,
    _: User = Depends(require_authenticated),
) -> CurrentTelemetryResponse:
    """Get the most recent in-memory telemetry event."""
    manager = request.app.state.telemetry_manager
    event = manager.get_current()
    return CurrentTelemetryResponse(event=event, available=event is not None)


@router.get("/history", response_model=HistoryResponse)
async def get_telemetry_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=5000, description="Number of events to return"),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    """Get persistent telemetry history, falling back to live in-memory history."""
    manager = request.app.state.telemetry_manager
    host_id = manager.persistence_host_id

    if host_id is None:
        events = manager.get_history(limit=limit)
    else:
        records = list(
            db.scalars(
                select(TelemetryRecord)
                .where(TelemetryRecord.host_id == host_id)
                .order_by(TelemetryRecord.timestamp.desc())
                .limit(limit)
            )
        )
        events = [_to_event(record) for record in reversed(records)]

        if not events:
            events = manager.get_history(limit=limit)

    return HistoryResponse(events=events, count=len(events), limit=limit)


@router.get("/stats", response_model=TelemetryStats)
async def get_telemetry_stats(
    request: Request,
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> TelemetryStats:
    """Get aggregated statistics from persistent telemetry history."""
    manager = request.app.state.telemetry_manager
    host_id = manager.persistence_host_id

    if host_id is None:
        return manager.get_stats()

    records = list(
        db.scalars(
            select(TelemetryRecord)
            .where(TelemetryRecord.host_id == host_id)
            .order_by(TelemetryRecord.timestamp.asc())
        )
    )
    if not records:
        return manager.get_stats()

    return compute_stats([_to_event(record) for record in records])
