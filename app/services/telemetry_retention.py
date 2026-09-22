"""Bounded background retention for high-volume telemetry records."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.db import TelemetryRecord
from app.services.platform_metrics import platform_metrics

logger = logging.getLogger(__name__)


class TelemetryRetentionService:
    """Delete old telemetry in bounded batches without blocking the event loop."""

    def __init__(
        self,
        *,
        retention_hours: int | None = None,
        interval_seconds: int | None = None,
        batch_size: int | None = None,
        max_batches_per_run: int | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.telemetry_retention_enabled
        self.retention_hours = retention_hours or settings.telemetry_retention_hours
        self.interval_seconds = interval_seconds or settings.telemetry_retention_cleanup_interval_seconds
        self.batch_size = batch_size or settings.telemetry_retention_batch_size
        self.max_batches_per_run = max_batches_per_run or settings.telemetry_retention_max_batches_per_run
        self._task: asyncio.Task | None = None
        self.last_run_at: datetime | None = None
        self.last_deleted: int = 0

    @property
    def running(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task is None:
            return
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_once(self, *, now: datetime | None = None) -> int:
        """Delete at most max_batches_per_run chunks and return deleted rows."""
        if not self.enabled:
            return 0

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(hours=self.retention_hours)

        deleted = 0
        try:
            for _ in range(self.max_batches_per_run):
                batch_deleted = await asyncio.to_thread(self._delete_batch, cutoff)
                if batch_deleted == 0:
                    break
                deleted += batch_deleted
                if batch_deleted < self.batch_size:
                    break
        except Exception:
            platform_metrics.increment("telemetry_retention_errors_total")
            logger.exception("Telemetry retention cleanup failed")
            raise

        self.last_run_at = current
        self.last_deleted = deleted
        platform_metrics.increment("telemetry_retention_runs_total")
        if deleted:
            platform_metrics.increment("telemetry_retention_deleted_total", deleted)
        return deleted

    def _delete_batch(self, cutoff: datetime) -> int:
        with SessionLocal() as db:
            ids = list(
                db.scalars(
                    select(TelemetryRecord.id)
                    .where(TelemetryRecord.timestamp < cutoff)
                    .order_by(TelemetryRecord.timestamp.asc(), TelemetryRecord.id.asc())
                    .limit(self.batch_size)
                )
            )
            if not ids:
                return 0

            result = db.execute(
                delete(TelemetryRecord).where(TelemetryRecord.id.in_(ids))
            )
            db.commit()
            return int(result.rowcount or 0)

    async def _worker(self) -> None:
        try:
            await self.run_once()
        except Exception:
            # The scheduled worker must remain alive after a transient database
            # failure; the error and metrics already record the failed run.
            pass

        while True:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled telemetry retention run failed")
