"""Validation and persistence of agent telemetry batches."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from app.models.db import Host, TelemetryRecord
from app.models.observability import IngestTelemetryBatch
from app.models.telemetry import TelemetryEvent
from app.services.platform_metrics import platform_metrics


class TelemetryIngestionService:
    """Persist bounded batches and hand events to the runtime processor."""

    def persist_batch(
        self,
        db: Session,
        *,
        host: Host,
        payload: IngestTelemetryBatch,
    ) -> list[TelemetryEvent]:
        events = [
            TelemetryEvent(
                timestamp=event.timestamp.astimezone(timezone.utc),
                sequence=event.sequence,
                cpu=event.cpu,
                memory=event.memory,
                temperature=event.temperature,
                network_mbps=event.network_mbps,
                requests_per_second=event.requests_per_second,
                error_rate=event.error_rate,
                latency_ms=event.latency_ms,
                host_id=host.id,
                source="agent",
                agent_version=payload.agent_version,
            )
            for event in payload.events
        ]

        rows = [
            {
                "host_id": host.id,
                "timestamp": event.timestamp,
                "sequence": event.sequence,
                "cpu": event.cpu,
                "memory": event.memory,
                "temperature": event.temperature,
                "network_mbps": event.network_mbps,
                "requests_per_second": event.requests_per_second,
                "error_rate": event.error_rate,
                "latency_ms": event.latency_ms,
            }
            for event in events
        ]
        db.execute(insert(TelemetryRecord), rows)
        host.last_seen_at = max(event.timestamp for event in events)
        if payload.agent_version:
            host.agent_version = payload.agent_version
        db.execute(
            update(Host)
            .where(Host.id == host.id)
            .values(last_seen_at=host.last_seen_at, agent_version=host.agent_version)
        )
        db.commit()
        platform_metrics.increment("telemetry_ingested_total", len(events))
        platform_metrics.increment("telemetry_ingestion_batches_total")
        return events
