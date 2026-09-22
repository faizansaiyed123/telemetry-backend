"""Validation and idempotent persistence of agent telemetry batches."""

from __future__ import annotations

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.db import Host, TelemetryRecord
from app.models.observability import IngestTelemetryBatch
from app.models.telemetry import TelemetryEvent
from app.services.platform_metrics import platform_metrics


class TelemetryIngestionService:
    """Validate, deduplicate, persist, and convert agent telemetry batches."""

    def persist_batch(
        self,
        db: Session,
        *,
        host: Host,
        payload: IngestTelemetryBatch,
    ) -> list[TelemetryEvent]:
        existing_sequences = set(
            db.scalars(
                select(TelemetryRecord.sequence).where(
                    TelemetryRecord.host_id == host.id,
                    TelemetryRecord.sequence.in_([event.sequence for event in payload.events]),
                )
            )
        )

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
            if event.sequence not in existing_sequences
        ]

        if not events:
            return []

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
        stmt = pg_insert(TelemetryRecord).values(rows).on_conflict_do_nothing(
            constraint="uq_telemetry_host_sequence"
        )
        result = db.execute(stmt)

        host.last_seen_at = max(event.timestamp for event in events)
        if payload.agent_version:
            host.agent_version = payload.agent_version
        db.commit()

        accepted = result.rowcount if result.rowcount is not None else len(events)
        platform_metrics.increment("telemetry_ingestion_batches_total")
        return events[:accepted]
