"""Self-observability API for the telemetry platform."""

from __future__ import annotations

import time
from datetime import datetime

import psutil
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import require_operator
from app.db.session import get_db
from app.models.db import User

router = APIRouter(prefix="/api/system", tags=["system"])


class PlatformMetricsResponse(BaseModel):
    timestamp: datetime
    uptime_seconds: float
    process_cpu_percent: float
    process_memory_mb: float
    database_connected: bool
    database_latency_ms: float | None
    telemetry_running: bool
    telemetry_rate: int
    events_generated: int
    events_ingested: int
    history_size: int
    websocket_clients: int
    active_alerts: int
    total_alerts: int
    enabled_alert_rules: int
    rule_evaluations: int
    rule_fires: int
    rule_resolves: int
    active_rule_alerts: int
    active_incidents: int
    incidents_created: int
    incidents_resolved: int
    persistence_queue_size: int
    persistence_persisted_events: int
    persistence_dropped_events: int
    alert_persistence_queue_size: int
    alert_persistence_dropped_events: int


@router.get("/metrics", response_model=PlatformMetricsResponse)
def platform_metrics(
    _: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> PlatformMetricsResponse:
    from app.main import app

    manager = app.state.telemetry_manager
    process = psutil.Process()
    db_ok = False
    db_latency_ms: float | None = None

    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    except Exception:
        db_ok = False

    return PlatformMetricsResponse(
        timestamp=datetime.now().astimezone(),
        uptime_seconds=manager.uptime_seconds,
        process_cpu_percent=process.cpu_percent(interval=None),
        process_memory_mb=round(process.memory_info().rss / 1024 / 1024, 2),
        database_connected=db_ok,
        database_latency_ms=db_latency_ms,
        telemetry_running=manager.running,
        telemetry_rate=manager.rate,
        events_generated=manager.events_generated,
        events_ingested=manager.events_ingested,
        history_size=manager.history_size,
        websocket_clients=manager.connected_clients,
        active_alerts=manager.active_alert_count,
        total_alerts=manager.total_alert_count,
        enabled_alert_rules=manager.rule_engine.enabled_rule_count,
        rule_evaluations=manager.rule_engine.evaluations,
        rule_fires=manager.rule_engine.fires,
        rule_resolves=manager.rule_engine.resolves,
        active_rule_alerts=manager.rule_engine.active_rule_alert_count,
        active_incidents=manager.incident_manager.active_count,
        incidents_created=manager.incident_manager.created_count,
        incidents_resolved=manager.incident_manager.resolved_count,
        persistence_queue_size=manager.persistence_queue_size,
        persistence_persisted_events=manager.persistence_persisted_events,
        persistence_dropped_events=manager.persistence_dropped_events,
        alert_persistence_queue_size=manager.alert_persistence_queue_size,
        alert_persistence_dropped_events=manager.alert_persistence_dropped_events,
    )


@router.get("/metrics/json")
def platform_metrics_json(
    _: User = Depends(require_operator),
    db: Session = Depends(get_db),
    include_process: bool = Query(default=True),
):
    """Compatibility alias for clients that prefer a dictionary-shaped response."""
    result = platform_metrics(_, db)
    data = result.model_dump()
    if not include_process:
        data.pop("process_cpu_percent", None)
        data.pop("process_memory_mb", None)
    return data
