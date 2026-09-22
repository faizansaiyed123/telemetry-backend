"""Telemetry REST API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
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


def _normalize_range(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    def normalize(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    normalized_start = normalize(start)
    normalized_end = normalize(end)
    if normalized_start is not None and normalized_end is not None and normalized_start >= normalized_end:
        raise HTTPException(status_code=422, detail="start must be earlier than end")
    return normalized_start, normalized_end


def _apply_range(stmt, *, host_id: str, start: datetime | None, end: datetime | None):
    stmt = stmt.where(TelemetryRecord.host_id == host_id)
    if start is not None:
        stmt = stmt.where(TelemetryRecord.timestamp >= start)
    if end is not None:
        stmt = stmt.where(TelemetryRecord.timestamp < end)
    return stmt


def _db_stats(
    db: Session,
    *,
    host_id: str,
    start: datetime | None,
    end: datetime | None,
) -> TelemetryStats | None:
    base = _apply_range(select(TelemetryRecord), host_id=host_id, start=start, end=end)
    summary_stmt = base.with_only_columns(
        func.count(TelemetryRecord.id),
        func.min(TelemetryRecord.cpu),
        func.max(TelemetryRecord.cpu),
        func.avg(TelemetryRecord.cpu),
        func.min(TelemetryRecord.memory),
        func.max(TelemetryRecord.memory),
        func.avg(TelemetryRecord.memory),
        func.min(TelemetryRecord.temperature),
        func.max(TelemetryRecord.temperature),
        func.avg(TelemetryRecord.temperature),
        func.min(TelemetryRecord.network_mbps),
        func.max(TelemetryRecord.network_mbps),
        func.avg(TelemetryRecord.network_mbps),
        func.min(TelemetryRecord.requests_per_second),
        func.max(TelemetryRecord.requests_per_second),
        func.avg(TelemetryRecord.requests_per_second),
        func.min(TelemetryRecord.error_rate),
        func.max(TelemetryRecord.error_rate),
        func.avg(TelemetryRecord.error_rate),
        func.min(TelemetryRecord.latency_ms),
        func.max(TelemetryRecord.latency_ms),
        func.avg(TelemetryRecord.latency_ms),
    )
    count_and_summary = db.execute(summary_stmt).one()
    count = int(count_and_summary[0] or 0)
    if count == 0:
        return None

    first = db.scalar(
        _apply_range(
            select(TelemetryRecord).order_by(TelemetryRecord.timestamp.asc(), TelemetryRecord.sequence.asc()).limit(1),
            host_id=host_id,
            start=start,
            end=end,
        )
    )
    latest = db.scalar(
        _apply_range(
            select(TelemetryRecord).order_by(TelemetryRecord.timestamp.desc(), TelemetryRecord.sequence.desc()).limit(1),
            host_id=host_id,
            start=start,
            end=end,
        )
    )
    if first is None or latest is None:
        return None

    values = count_and_summary
    metric_values = {
        "cpu": (values[1], values[2], values[3], latest.cpu, first.cpu),
        "memory": (values[4], values[5], values[6], latest.memory, first.memory),
        "temperature": (values[7], values[8], values[9], latest.temperature, first.temperature),
        "network_mbps": (values[10], values[11], values[12], latest.network_mbps, first.network_mbps),
        "requests_per_second": (values[13], values[14], values[15], latest.requests_per_second, first.requests_per_second),
        "error_rate": (values[16], values[17], values[18], latest.error_rate, first.error_rate),
        "latency_ms": (values[19], values[20], values[21], latest.latency_ms, first.latency_ms),
    }

    stats = {}
    for metric, (minimum, maximum, average, latest_value, first_value) in metric_values.items():
        pct_change = None if first_value == 0 else ((latest_value - first_value) / first_value) * 100
        stats[metric] = {
            "min": minimum,
            "max": maximum,
            "avg": round(float(average), 2),
            "latest": latest_value,
            "pct_change": round(pct_change, 2) if pct_change is not None else None,
        }

    return TelemetryStats(
        count=count,
        cpu=stats["cpu"],
        memory=stats["memory"],
        temperature=stats["temperature"],
        network_mbps=stats["network_mbps"],
        requests_per_second=stats["requests_per_second"],
        error_rate=stats["error_rate"],
        latency_ms=stats["latency_ms"],
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
    start: datetime | None = Query(default=None, description="Inclusive UTC start timestamp"),
    end: datetime | None = Query(default=None, description="Exclusive UTC end timestamp"),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    start, end = _normalize_range(start, end)
    manager = request.app.state.telemetry_manager
    if start is None and end is None and manager.persistence_host_id is None and host_id is None:
        events = manager.get_history(limit=limit)
    else:
        target_host_id = host_id or manager.persistence_host_id
        if target_host_id is None:
            events = manager.get_history(limit=limit, host_id=host_id)
        else:
            stmt = (
                _apply_range(
                    select(TelemetryRecord).order_by(
                        TelemetryRecord.timestamp.desc(),
                        TelemetryRecord.sequence.desc(),
                    ),
                    host_id=target_host_id,
                    start=start,
                    end=end,
                )
                .limit(limit)
            )
            records = list(db.scalars(stmt))
            events = [_to_event(record) for record in reversed(records)]
            if not events:
                in_memory = manager.get_history(limit=limit, host_id=target_host_id)
                events = [
                    event
                    for event in in_memory
                    if (start is None or event.timestamp >= start)
                    and (end is None or event.timestamp < end)
                ]

    return HistoryResponse(events=events, count=len(events), limit=limit)


@router.get("/stats", response_model=TelemetryStats)
async def get_telemetry_stats(
    request: Request,
    host_id: str | None = Query(default=None),
    start: datetime | None = Query(default=None, description="Inclusive UTC start timestamp"),
    end: datetime | None = Query(default=None, description="Exclusive UTC end timestamp"),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> TelemetryStats:
    start, end = _normalize_range(start, end)
    manager = request.app.state.telemetry_manager
    target_host_id = host_id or manager.persistence_host_id
    if target_host_id is None:
        if start is not None or end is not None:
            filtered = [
                event for event in manager.get_history(manager.max_history_size, host_id=host_id)
                if (start is None or event.timestamp >= start) and (end is None or event.timestamp < end)
            ]
            return compute_stats(filtered)
        return manager.get_stats(host_id=host_id)

    result = _db_stats(db, host_id=target_host_id, start=start, end=end)
    if result is not None:
        return result
    if start is not None or end is not None:
        filtered = [
            event
            for event in manager.get_history(manager.max_history_size, host_id=host_id)
            if (start is None or event.timestamp >= start)
            and (end is None or event.timestamp < end)
        ]
        return compute_stats(filtered)
    return manager.get_stats(host_id=host_id)
