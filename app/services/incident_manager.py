"""Incident correlation service for active alert clusters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.alerts import Alert, Severity
from app.models.db import AlertRecord
from app.models.observability import IncidentRecord
from app.utils.time import utc_now


@dataclass(slots=True)
class IncidentState:
    id: str
    host_id: str | None
    title: str
    severity: Severity
    status: str
    started_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    active_alert_ids: set[str] = field(default_factory=set)
    total_alerts: int = 0


class IncidentManager:
    """Correlate concurrent alerts by host into durable incidents."""

    def __init__(self, correlation_window_seconds: float = 300.0) -> None:
        self.correlation_window = timedelta(seconds=correlation_window_seconds)
        self._states: dict[str, IncidentState] = {}
        self._lock = asyncio.Lock()
        self.created_count = 0
        self.resolved_count = 0

    @property
    def active_count(self) -> int:
        return sum(state.status != "resolved" for state in self._states.values())

    async def handle_alert(self, alert: Alert) -> Alert:
        async with self._lock:
            scope = alert.host_id or "__global__"
            state = self._states.get(scope)
            if (
                state is None
                or state.status == "resolved"
                or alert.timestamp - state.started_at > self.correlation_window
            ):
                state = IncidentState(
                    id=str(uuid4()),
                    host_id=alert.host_id,
                    title="Infrastructure alert cluster",
                    severity=alert.severity,
                    status="open",
                    started_at=alert.timestamp,
                    active_alert_ids={alert.id},
                    total_alerts=1,
                )
                self._states[scope] = state
                self.created_count += 1
            else:
                state.active_alert_ids.add(alert.id)
                state.total_alerts += 1
                state.severity = self._max_severity(state.severity, alert.severity)

            alert.incident_id = state.id

        await self._persist_state(state, alert.id)
        return alert

    async def handle_resolution(self, alert: Alert) -> IncidentState | None:
        if not alert.incident_id:
            return None

        async with self._lock:
            state = next(
                (item for item in self._states.values() if item.id == alert.incident_id),
                None,
            )
            if state is None:
                state = await asyncio.to_thread(self._load_state, alert.incident_id)
                if state is None:
                    return None
                self._states[state.host_id or "__global__"] = state

            state.active_alert_ids.discard(alert.id)
            if not state.active_alert_ids and state.status != "resolved":
                state.status = "resolved"
                state.resolved_at = alert.resolved_at or utc_now()
                self.resolved_count += 1

        if state.status == "resolved":
            await self._persist_state(state, alert.id)
        return state

    async def acknowledge(self, incident_id: str) -> IncidentState | None:
        async with self._lock:
            state = next(
                (item for item in self._states.values() if item.id == incident_id),
                None,
            )
            if state is None:
                state = await asyncio.to_thread(self._load_state, incident_id)
                if state is None:
                    return None
                self._states[state.host_id or "__global__"] = state

            if state.status == "resolved":
                return state

            state.status = "acknowledged"
            state.acknowledged_at = utc_now()

        await self._persist_state(state, None)
        return state

    async def resolve_manually(self, incident_id: str) -> IncidentState | None:
        async with self._lock:
            state = next(
                (item for item in self._states.values() if item.id == incident_id),
                None,
            )
            if state is None:
                state = await asyncio.to_thread(self._load_state, incident_id)
                if state is None:
                    return None
                self._states[state.host_id or "__global__"] = state

            if state.status == "resolved":
                return state

            state.status = "resolved"
            state.resolved_at = utc_now()

        await self._persist_state(state, None)
        return state

    async def load_open(self) -> None:
        states = await asyncio.to_thread(self._load_open_states)
        async with self._lock:
            self._states = {state.host_id or "__global__": state for state in states}

    async def _persist_state(self, state: IncidentState, alert_id: str | None) -> None:
        def persist() -> None:
            with SessionLocal() as db:
                existing = db.get(IncidentRecord, state.id)
                if existing is None:
                    db.add(
                        IncidentRecord(
                            id=state.id,
                            host_id=state.host_id,
                            title=state.title,
                            severity=state.severity.value,
                            status=state.status,
                            started_at=state.started_at,
                            acknowledged_at=state.acknowledged_at,
                            resolved_at=state.resolved_at,
                            created_at=state.started_at,
                            updated_at=utc_now(),
                        )
                    )
                else:
                    existing.severity = state.severity.value
                    existing.status = state.status
                    existing.acknowledged_at = state.acknowledged_at
                    existing.resolved_at = state.resolved_at
                    existing.updated_at = utc_now()

                if alert_id:
                    db.execute(
                        update(AlertRecord)
                        .where(AlertRecord.id == alert_id)
                        .values(incident_id=state.id)
                    )
                db.commit()

        await asyncio.to_thread(persist)

    @staticmethod
    def _load_state(incident_id: str) -> IncidentState | None:
        with SessionLocal() as db:
            record = db.get(IncidentRecord, incident_id)
            if record is None:
                return None
            alerts = list(
                db.scalars(
                    select(AlertRecord).where(
                        AlertRecord.incident_id == incident_id,
                        AlertRecord.status == "active",
                    )
                )
            )
            all_alerts = list(
                db.scalars(select(AlertRecord).where(AlertRecord.incident_id == incident_id))
            )
            return IncidentState(
                id=record.id,
                host_id=record.host_id,
                title=record.title,
                severity=Severity(record.severity),
                status=record.status,
                started_at=record.started_at,
                acknowledged_at=record.acknowledged_at,
                resolved_at=record.resolved_at,
                active_alert_ids={alert.id for alert in alerts},
                total_alerts=len(all_alerts),
            )

    @staticmethod
    def _load_open_states() -> list[IncidentState]:
        with SessionLocal() as db:
            records = list(
                db.scalars(
                    select(IncidentRecord).where(IncidentRecord.status != "resolved")
                )
            )
            states: list[IncidentState] = []
            for record in records:
                alerts = list(
                    db.scalars(
                        select(AlertRecord).where(
                            AlertRecord.incident_id == record.id,
                            AlertRecord.status == "active",
                        )
                    )
                )
                all_alerts = list(
                    db.scalars(select(AlertRecord).where(AlertRecord.incident_id == record.id))
                )
                states.append(
                    IncidentState(
                        id=record.id,
                        host_id=record.host_id,
                        title=record.title,
                        severity=Severity(record.severity),
                        status=record.status,
                        started_at=record.started_at,
                        acknowledged_at=record.acknowledged_at,
                        resolved_at=record.resolved_at,
                        active_alert_ids={alert.id for alert in alerts},
                        total_alerts=len(all_alerts),
                    )
                )
            return states

    @staticmethod
    def _max_severity(left: Severity, right: Severity) -> Severity:
        order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
        return left if order[left] >= order[right] else right
