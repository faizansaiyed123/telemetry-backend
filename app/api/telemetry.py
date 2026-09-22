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
        host_id=record.host_id,
        source=record.source,
        agent_version=record.agent_version,
    )


@router.get("/current", response_model=CurrentTelemetryResponse)
async def get_current_telemetry(
    request: Request,
    host_id: str | None = Query(default=None),
    _: User = Depends(require_authenticated),
) -> CurrentTelemetryResponse:
    manager = request.app.state.telemetry_manager
    event = manager.get_current(host_id=host_id)
    return CurrentTelemetryResponse(event=event, available=event is not None)


@router.get("/history", response_model=HistoryResponse)
async def get_telemetry_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=5000, description="Number of events to return"),
    host_id: str | None = Query(default=None),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    manager = request.app.state.telemetry_manager

    if manager.persistence_host_id is None and host_id is None:
        events = manager.get_history(limit=limit)
    else:
        target_host_id = host_id or manager.persistence_host_id
        records = list(
            db.scalars(
                select(TelemetryRecord)
                .where(TelemetryRecord.host_id == target_host_id)
                .order_by(TelemetryRecord.timestamp.desc())
                .limit(limit)
            )
        )
        events = [_to_event(record) for record in reversed(records)]
        if not events and host_id is None:
            events = manager.get_history(limit=limit)

    return HistoryResponse(events=events, count=len(events), limit=limit)


@router.get("/stats", response_model=TelemetryStats)
async def get_telemetry_stats(
    request: Request,
    host_id: str | None = Query(default=None),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> TelemetryStats:
    manager = request.app.state.telemetry_manager

    if manager.persistence_host_id is None and host_id is None:
        return manager.get_stats()

    target_host_id = host_id or manager.persistence_host_id
    records = list(
        db.scalars(
            select(TelemetryRecord)
            .where(TelemetryRecord.host_id == target_host_id)
            .order_by(TelemetryRecord.timestamp.asc())
        )
    )
    if not records:
        return manager.get_stats(host_id=host_id)
    return compute_stats([_to_event(record) for record in records])
