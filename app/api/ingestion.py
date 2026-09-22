"""Machine telemetry ingestion API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.db import ApiKey, Host
from app.models.observability import IngestResponse, IngestTelemetryBatch
from app.services.api_key_service import telemetry_api_key
from app.services.platform_metrics import platform_metrics
from app.services.telemetry_ingestion import TelemetryIngestionService

router = APIRouter(prefix="/api/ingest/v1", tags=["ingestion"])


@router.post("/telemetry", response_model=IngestResponse)
async def ingest_telemetry(
    payload: IngestTelemetryBatch,
    request: Request,
    secret: str = Depends(telemetry_api_key),
    db: Session = Depends(get_db),
) -> IngestResponse:
    key_hash = __import__("hashlib").sha256(secret.encode("utf-8")).hexdigest()
    key = db.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )
    if key is None:
        platform_metrics.increment("telemetry_ingestion_rejected_total")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid telemetry API key")

    host = db.get(Host, key.host_id)
    if host is None or not host.is_active:
        platform_metrics.increment("telemetry_ingestion_rejected_total")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telemetry host is inactive")

    service = TelemetryIngestionService()
    events = service.persist_batch(db, host=host, payload=payload)

    manager = request.app.state.telemetry_manager
    for event in events:
        await manager.process_event(event, persist=False)

    key.last_used_at = events[-1].timestamp
    db.commit()

    return IngestResponse(
        host_id=host.id,
        accepted=len(events),
        last_sequence=events[-1].sequence,
    )
