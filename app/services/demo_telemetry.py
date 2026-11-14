"""Temporary recruiter telemetry runtimes.

Each demo identity gets an independent in-process TelemetryManager with its own
generator state, anomaly detector, alerts and WebSocket manager. Demo runtimes
never use the production persistence host or production database state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.db import User
from app.services.telemetry_manager import TelemetryManager


@dataclass
class DemoRuntime:
    user_id: str
    expires_at: datetime
    manager: TelemetryManager


class DemoTelemetryRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, DemoRuntime] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        user_id: str,
        expires_at: datetime,
    ) -> TelemetryManager:
        settings = get_settings()
        manager = TelemetryManager(
            max_history_size=min(settings.max_history_size, 1200),
            telemetry_rate=min(settings.telemetry_rate, 25),
            max_rate=min(settings.max_telemetry_rate, 50),
            anomaly_threshold=settings.anomaly_z_threshold,
            source_mode="synthetic",
            persistence_enabled=False,
        )
        await manager.start()
        async with self._lock:
            previous = self._sessions.get(user_id)
            if previous is not None:
                await previous.manager.stop()
            self._sessions[user_id] = DemoRuntime(
                user_id=user_id,
                expires_at=expires_at,
                manager=manager,
            )
        return manager

    async def get(self, user_id: str) -> TelemetryManager | None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            runtime = self._sessions.get(user_id)
            if runtime is None:
                return None
            if runtime.expires_at <= now:
                self._sessions.pop(user_id, None)
                await runtime.manager.stop()
                return None
            return runtime.manager

    async def end(self, user_id: str) -> None:
        async with self._lock:
            runtime = self._sessions.pop(user_id, None)
        if runtime is not None:
            await runtime.manager.stop()

    async def sweep(self, db: Session) -> None:
        now = datetime.now(timezone.utc)
        expired_users = list(
            db.scalars(
                select(User).where(
                    User.is_demo.is_(True),
                    User.demo_expires_at.is_not(None),
                    User.demo_expires_at <= now,
                )
            )
        )
        for user in expired_users:
            await self.end(user.id)
            db.delete(user)
        if expired_users:
            db.commit()

    async def stop_all(self) -> None:
        async with self._lock:
            runtimes = list(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(
            *(runtime.manager.stop() for runtime in runtimes),
            return_exceptions=True,
        )

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)
