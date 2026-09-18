"""Background persistence for generated telemetry events."""

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
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        await self._flush()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def enqueue(self, event: TelemetryEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_events += 1
            logger.warning("Telemetry persistence queue full; dropping event sequence=%s", event.sequence)

    async def _worker(self) -> None:
        while not self._stopping:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                batch = [event]
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await self._persist(batch)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telemetry persistence worker failed; retrying")
                await asyncio.sleep(1)

    async def _flush(self) -> None:
        while not self._queue.empty():
            batch: list[TelemetryEvent] = []
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if batch:
                try:
                    await self._persist(batch)
                except Exception:
                    logger.exception("Final telemetry persistence flush failed")
                    break

    async def _persist(self, events: Sequence[TelemetryEvent]) -> None:
        rows = [
            {
                "host_id": self.host_id,
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
        with SessionLocal() as db:
            db.execute(insert(TelemetryRecord), rows)
            db.commit()
        self.persisted_events += len(rows)
