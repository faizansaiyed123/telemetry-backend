"""Machine telemetry ingestion API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.dependencies import IngestCredential, require_ingest_credential
from app.models.telemetry import TelemetryEvent

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


class IngestTelemetryEvent(BaseModel):
    timestamp: datetime
    sequence: int = Field(ge=0)
    cpu: float = Field(ge=0, le=100)
    memory: float = Field(ge=0, le=100)
    temperature: float = Field(ge=0, le=120)
    network_mbps: float = Field(ge=0, le=10000)
    requests_per_second: int = Field(default=0, ge=0, le=1_000_000)
    error_rate: float = Field(default=0, ge=0, le=100)
    latency_ms: float = Field(default=0, ge=0, le=10_000)

    def to_event(self, host_id: str) -> TelemetryEvent:
        return TelemetryEvent(
            timestamp=self.timestamp,
            sequence=self.sequence,
            cpu=self.cpu,
            memory=self.memory,
            temperature=self.temperature,
            network_mbps=self.network_mbps,
            requests_per_second=self.requests_per_second,
            error_rate=self.error_rate,
            latency_ms=self.latency_ms,
            host_id=host_id,
            source="agent",
        )


class IngestTelemetryRequest(BaseModel):
    events: list[IngestTelemetryEvent] = Field(min_length=1, max_length=500)
    agent_version: str = Field(default="unknown", min_length=1, max_length=64)


class IngestTelemetryResponse(BaseModel):
    status: str
    accepted: int
    queued: int
    dropped: int
    host_id: str
    agent_version: str


@router.post("/telemetry", response_model=IngestTelemetryResponse, status_code=202)
async def ingest_telemetry(
    payload: IngestTelemetryRequest,
    request: Request,
    credential: IngestCredential = Depends(require_ingest_credential),
) -> IngestTelemetryResponse:
    manager = request.app.state.telemetry_manager
    events = [item.to_event(credential.host_id) for item in payload.events]
    queued = await manager.ingest_external(events, credential.host_id)
    dropped = len(events) - queued
    return IngestTelemetryResponse(
        status="accepted",
        accepted=len(events) - dropped,
        queued=queued,
        dropped=dropped,
        host_id=credential.host_id,
        agent_version=payload.agent_version,
    )
