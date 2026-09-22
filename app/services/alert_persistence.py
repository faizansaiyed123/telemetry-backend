"""Background persistence for alert lifecycle events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import update

from app.db.session import SessionLocal
from app.models.alerts import Alert
from app.models.db import AlertRecord

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AlertPersistenceEvent:
    alert: Alert
    host_id: str | None


class AlertPersistence:
    """Persist alert create/update events off the telemetry generation path."""

    def __init__(self, max_queue_size: int = 2_000) -> None:
        self._queue: asyncio.Queue[AlertPersistenceEvent] = asyncio.Queue(maxsize=max_queue_size)
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

        task = self._task
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Alert persistence worker did not drain within shutdown timeout; cancelling it")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    def enqueue(self, alert: Alert, host_id: str | None) -> None:
        try:
            self._queue.put_nowait(
                AlertPersistenceEvent(alert=alert.model_copy(deep=True), host_id=host_id)
            )
        except asyncio.QueueFull:
            self.dropped_events += 1
            logger.warning("Alert persistence queue full; dropping alert id=%s", alert.id)

    async def _worker(self) -> None:
        retry_item: AlertPersistenceEvent | None = None

        while True:
            if retry_item is None:
                if self._stopping and self._queue.empty():
                    break

                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
            else:
                item = retry_item
                retry_item = None

            try:
                await self._persist(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Alert persistence worker failed; retaining item for retry")
                retry_item = item
                await asyncio.sleep(1)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def _persist(self, item: AlertPersistenceEvent) -> None:
        alert = item.alert
        with SessionLocal() as db:
            existing = db.get(AlertRecord, alert.id)
            if existing is None:
                db.add(
                    AlertRecord(
                        id=alert.id,
                        host_id=item.host_id,
                        metric=alert.metric,
                        value=alert.value,
                        baseline=alert.baseline,
                        severity=alert.severity.value,
                        message=alert.message,
                        status="resolved" if alert.resolved else "active",
                        acknowledged=alert.acknowledged,
                        timestamp=alert.timestamp,
                        resolved_at=alert.resolved_at,
                    )
                )
            else:
                db.execute(
                    update(AlertRecord)
                    .where(AlertRecord.id == alert.id)
                    .values(
                        value=alert.value,
                        baseline=alert.baseline,
                        severity=alert.severity.value,
                        message=alert.message,
                        status="resolved" if alert.resolved else "active",
                        acknowledged=existing.acknowledged or alert.acknowledged,
                        resolved_at=alert.resolved_at,
                    )
                )
            db.commit()

        self.persisted_events += 1
