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
        """Persist a batch exactly once per host/sequence and return inserted events."""
        by_sequence = {}
        for item in payload.events:
            # Preserve the first event for a sequence in a single request. The
            # database uniqueness constraint is the final guard against races
            # between concurrent agent requests.
            by_sequence.setdefault(item.sequence, item)

        sequences = list(by_sequence)
        existing_sequences = set(
            db.scalars(
                select(TelemetryRecord.sequence).where(
                    TelemetryRecord.host_id == host.id,
                    TelemetryRecord.sequence.in_(sequences),
                )
            )
        )

        candidates = [
            item
            for sequence, item in by_sequence.items()
            if sequence not in existing_sequences
        ]
        if not candidates:
            platform_metrics.increment("telemetry_ingestion_batches_total")
            return []

        events_by_sequence = {
            item.sequence: TelemetryEvent(
                timestamp=item.timestamp.astimezone(timezone.utc),
                sequence=item.sequence,
                cpu=item.cpu,
                memory=item.memory,
                temperature=item.temperature,
                network_mbps=item.network_mbps,
                requests_per_second=item.requests_per_second,
                error_rate=item.error_rate,
                latency_ms=item.latency_ms,
                host_id=host.id,
                source="agent",
                agent_version=payload.agent_version,
            )
            for item in candidates
        }

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
                "source": event.source,
                "agent_version": event.agent_version,
                "source": event.source,
                "agent_version": event.agent_version,
            }
            for event in events_by_sequence.values()
        ]

        result = db.execute(
            pg_insert(TelemetryRecord)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_telemetry_host_sequence")
            .returning(TelemetryRecord.sequence)
        )
        inserted_sequences = set(result.scalars().all())

        accepted_events = [
            events_by_sequence[sequence]
            for sequence in events_by_sequence
            if sequence in inserted_sequences
        ]

        if accepted_events:
            host.last_seen_at = max(event.timestamp for event in accepted_events)
            if payload.agent_version:
                host.agent_version = payload.agent_version

        db.commit()
        platform_metrics.increment("telemetry_ingestion_batches_total")
        return accepted_events
