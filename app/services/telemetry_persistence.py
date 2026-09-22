"""Background persistence for generated telemetry events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import SessionLocal
from app.models.db import Host, TelemetryRecord
from app.models.telemetry import TelemetryEvent
from app.services.platform_metrics import platform_metrics

logger = logging.getLogger(__name__)


class TelemetryPersistence:
    """Persist telemetry off the generation path using a bounded async queue."""

    def __init__(self, host_id: str, max_queue_size: int = 10_000, batch_size: int = 50) -> None:
        self.host_id = host_id
        self._queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=max_queue_size)
        self._batch_size = batch_size
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.dropped_events = 0
        self.persisted_events = 0

    async def start(self) -> None:
        """Start the persistence worker once."""
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Stop the worker after draining queued telemetry where possible."""
        self._stopping = True
        if self._task is None:
            return

        task = self._task
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Telemetry persistence worker did not drain within shutdown timeout; cancelling it"
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    def enqueue(self, event: TelemetryEvent) -> None:
        """Queue an event without ever blocking the telemetry generation path."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_events += 1
            platform_metrics.increment("telemetry_persistence_dropped_total")
            logger.warning(
                "Telemetry persistence queue full; dropping event sequence=%s",
                event.sequence,
            )

    async def _worker(self) -> None:
        """Persist queued events until shutdown is requested and the queue is empty."""
        retry_batch: list[TelemetryEvent] | None = None

        while True:
            if retry_batch is None:
                if self._stopping and self._queue.empty():
                    break

                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise

                batch = [event]
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            else:
                batch = retry_batch
                retry_batch = None

            try:
                await self._persist(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telemetry persistence worker failed; retaining batch for retry")
                retry_batch = batch
                await asyncio.sleep(1)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def _persist(self, events: Sequence[TelemetryEvent]) -> None:
        """Persist one batch in a short-lived synchronous database session."""
        rows = [
            {
                "host_id": event.host_id or self.host_id,
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
            }
            for event in events
        ]
        with SessionLocal() as db:
            result = db.execute(
                pg_insert(TelemetryRecord)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_telemetry_host_sequence")
            )
            rowcount = result.rowcount
            inserted_count = rowcount if isinstance(rowcount, int) and rowcount >= 0 else len(rows)
            host_updates: dict[str, object] = {}
            for event in events:
                host_id = event.host_id or self.host_id
                previous = host_updates.get(host_id)
                if previous is None or event.timestamp > previous:
                    host_updates[host_id] = event.timestamp
            for host_id, last_seen_at in host_updates.items():
                db.execute(
                    update(Host)
                    .where(Host.id == host_id)
                    .values(last_seen_at=last_seen_at)
                )
            db.commit()
        self.persisted_events += inserted_count
        platform_metrics.increment("telemetry_persisted_total", inserted_count)
