"""Machine telemetry ingestion API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.db import ApiKey, Host
from app.models.observability import IngestResponse, IngestTelemetryBatch
from app.services.api_key_service import telemetry_api_key
from app.services.platform_metrics import platform_metrics
from app.services.telemetry_ingestion import TelemetryIngestionService
from app.utils.time import utc_now

router = APIRouter(prefix="/api/ingest/v1", tags=["ingestion"])


@router.post("/telemetry", response_model=IngestResponse)
async def ingest_telemetry(
    payload: IngestTelemetryBatch,
    request: Request,
    key: ApiKey = Depends(telemetry_api_key),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """Accept a bounded telemetry batch from a host-scoped agent credential."""
    host = db.get(Host, key.host_id)
    if host is None or not host.is_active:
        platform_metrics.increment("telemetry_ingestion_rejected_total")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telemetry host is inactive",
        )

    events = TelemetryIngestionService().persist_batch(
        db,
        host=host,
        payload=payload,
    )

    # Persist credential activity only after the telemetry transaction has
    # succeeded. The key secret itself is never stored or returned here.
    key.last_used_at = utc_now()
    db.commit()

    manager = request.app.state.telemetry_manager
    for event in events:
        await manager.process_event(event, persist=False)

    accepted = len(events)
    received = len(payload.events)
    deduplicated = received - accepted
    if deduplicated:
        platform_metrics.increment("telemetry_ingestion_deduplicated_total", deduplicated)

    return IngestResponse(
        host_id=host.id,
        received=received,
        accepted=accepted,
        deduplicated=deduplicated,
        last_sequence=max(item.sequence for item in payload.events),
    )
