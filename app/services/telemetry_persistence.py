"""Background persistence for generated and ingested telemetry events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy import insert

from app.db.session import SessionLocal
from app.models.db import TelemetryRecord
from app.models.telemetry import TelemetryEvent

logger = logging.getLogger(__name__)


class TelemetryPersistence:
    """Persist telemetry off the hot path using a bounded async queue."""

    def __init__(self, host_id: str | None = None, max_queue_size: int = 10_000, batch_size: int = 50) -> None:
        self.host_id = host_id
        self._queue: asyncio.Queue[tuple[str, TelemetryEvent]] = asyncio.Queue(maxsize=max_queue_size)
        self._batch_size = batch_size
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.dropped_events = 0
        self.persisted_events = 0

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

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

    def enqueue(self, event: TelemetryEvent, host_id: str | None = None) -> bool:
        """Queue an event without blocking the telemetry generation path."""
        target_host_id = host_id or event.host_id or self.host_id
        if target_host_id is None:
            self.dropped_events += 1
            logger.warning("Telemetry persistence skipped: event has no host identity")
            return False
        try:
            self._queue.put_nowait((target_host_id, event))
            return True
        except asyncio.QueueFull:
            self.dropped_events += 1
            logger.warning(
                "Telemetry persistence queue full; dropping event sequence=%s host_id=%s",
                event.sequence,
                target_host_id,
            )
            return False

    async def _worker(self) -> None:
        """Persist queued events until shutdown is requested and the queue is empty."""
        retry_batch: list[tuple[str, TelemetryEvent]] | None = None

        while True:
            if retry_batch is None:
                if self._stopping and self._queue.empty():
                    break

                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise

                batch = [item]
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

    async def _persist(self, events: Sequence[tuple[str, TelemetryEvent]]) -> None:
        rows = [
            {
                "host_id": host_id,
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
            for host_id, event in events
        ]
        with SessionLocal() as db:
            db.execute(insert(TelemetryRecord), rows)
            db.commit()
        self.persisted_events += len(rows)
