"""In-memory incident correlation with asynchronous persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.db import AlertRecord, Incident, IncidentAlert
from app.models.alerts import Alert
from app.services.platform_metrics import platform_metrics


SEVERITY_RANK = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
CORRELATION_WINDOW = timedelta(minutes=5)


@dataclass(slots=True)
class IncidentState:
    id: str
    host_id: str | None
    title: str
    status: str
    severity: str
    first_seen_at: datetime
    last_seen_at: datetime
    alert_ids: set[str] = field(default_factory=set)
    active_alert_ids: set[str] = field(default_factory=set)
    resolved_at: datetime | None = None

    def snapshot(self) -> "IncidentState":
        """Return an immutable-at-enqueue-time copy for background persistence."""
        return IncidentState(
            id=self.id,
            host_id=self.host_id,
            title=self.title,
            status=self.status,
            severity=self.severity,
            first_seen_at=self.first_seen_at,
            last_seen_at=self.last_seen_at,
            alert_ids=set(self.alert_ids),
            active_alert_ids=set(self.active_alert_ids),
            resolved_at=self.resolved_at,
        )


@dataclass(slots=True)
class IncidentPersistenceEvent:
    incident: IncidentState
    alert_id: str | None = None


class IncidentPersistence:
    """Persist incident state without blocking telemetry generation."""

    def __init__(self, max_queue_size: int = 2_000) -> None:
        self._queue: asyncio.Queue[IncidentPersistenceEvent] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.dropped_events = 0
        self.persisted_events = 0

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

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
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    def enqueue(self, event: IncidentPersistenceEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_events += 1
            platform_metrics.increment("incident_persistence_dropped_total")

    async def _worker(self) -> None:
        retry: IncidentPersistenceEvent | None = None
        while True:
            if retry is None:
                if self._stopping and self._queue.empty():
                    return
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
            else:
                item = retry
                retry = None

            try:
                await self._persist(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                retry = item
                await asyncio.sleep(1)

    async def _persist(self, event: IncidentPersistenceEvent) -> None:
        incident = event.incident
        with SessionLocal() as db:
            existing = db.get(Incident, incident.id)
            if existing is None:
                db.add(
                    Incident(
                        id=incident.id,
                        host_id=incident.host_id,
                        title=incident.title,
                        status=incident.status,
                        severity=incident.severity,
                        first_seen_at=incident.first_seen_at,
                        last_seen_at=incident.last_seen_at,
                        resolved_at=incident.resolved_at,
                    )
                )
            else:
                existing.host_id = incident.host_id
                existing.title = incident.title
                existing.status = incident.status
                existing.severity = incident.severity
                existing.first_seen_at = incident.first_seen_at
                existing.last_seen_at = incident.last_seen_at
                existing.resolved_at = incident.resolved_at
                existing.updated_at = datetime.now(timezone.utc)

            if event.alert_id is not None:
                if db.get(AlertRecord, event.alert_id) is None:
                    raise RuntimeError("alert persistence has not committed the referenced alert yet")
                exists = db.scalar(
                    select(IncidentAlert).where(
                        IncidentAlert.incident_id == incident.id,
                        IncidentAlert.alert_id == event.alert_id,
                    )
                )
                if exists is None:
                    db.add(IncidentAlert(incident_id=incident.id, alert_id=event.alert_id))

            db.commit()
        self.persisted_events += 1


class IncidentEngine:
    """Correlate alerts by host and a short temporal window."""

    def __init__(self, persistence: IncidentPersistence | None = None) -> None:
        self._incidents: dict[str, IncidentState] = {}
        self._alert_to_incident: dict[str, str] = {}
        self._persistence = persistence

    async def start(self) -> None:
        if self._persistence is not None:
            await self._persistence.start()
        self.hydrate()

    async def stop(self) -> None:
        if self._persistence is not None:
            await self._persistence.stop()

    def hydrate(self) -> None:
        with SessionLocal() as db:
            rows = db.scalars(
                select(Incident).order_by(Incident.last_seen_at.desc())
            )
            for incident in rows:
                state = IncidentState(
                    id=incident.id,
                    host_id=incident.host_id,
                    title=incident.title,
                    status=incident.status,
                    severity=incident.severity,
                    first_seen_at=incident.first_seen_at,
                    last_seen_at=incident.last_seen_at,
                    resolved_at=incident.resolved_at,
                )
                for link in db.scalars(
                    select(IncidentAlert).where(IncidentAlert.incident_id == incident.id)
                ):
                    state.alert_ids.add(link.alert_id)
                    self._alert_to_incident[link.alert_id] = incident.id
                active_alert_ids = set(
                    db.scalars(
                        select(AlertRecord.id).where(
                            AlertRecord.id.in_(state.alert_ids),
                            AlertRecord.status == "active",
                        )
                    )
                )
                state.active_alert_ids = active_alert_ids
                self._incidents[incident.id] = state

    def _candidate(self, alert: Alert) -> IncidentState | None:
        for incident in self._incidents.values():
            if incident.host_id != alert.host_id:
                continue
            if incident.status == "resolved":
                continue
            delta = abs(alert.timestamp - incident.last_seen_at)
            if delta <= CORRELATION_WINDOW:
                return incident
        return None

    def on_alert_created(self, alert: Alert) -> IncidentState:
        incident = self._candidate(alert)
        if incident is None:
            incident = IncidentState(
                id=str(uuid4()),
                host_id=alert.host_id,
                title=f"Correlated incident on {alert.host_id or 'system'}",
                status="open",
                severity=alert.severity.value,
                first_seen_at=alert.timestamp,
                last_seen_at=alert.timestamp,
            )
            self._incidents[incident.id] = incident
            platform_metrics.increment("incident_created_total")
        else:
            if SEVERITY_RANK[alert.severity.value] > SEVERITY_RANK[incident.severity]:
                incident.severity = alert.severity.value
            # Acknowledgement applies to the current alert set. A new alert
            # reopens the incident so operators cannot miss a fresh signal.
            if incident.status == "acknowledged":
                incident.status = "open"
            incident.last_seen_at = max(incident.last_seen_at, alert.timestamp)

        incident.alert_ids.add(alert.id)
        incident.active_alert_ids.add(alert.id)
        self._alert_to_incident[alert.id] = incident.id
        if self._persistence is not None:
            self._persistence.enqueue(
                IncidentPersistenceEvent(incident=incident.snapshot(), alert_id=alert.id)
            )
        return incident

    def on_alert_resolved(self, alert: Alert) -> IncidentState | None:
        incident_id = self._alert_to_incident.get(alert.id)
        if incident_id is None:
            return None
        incident = self._incidents.get(incident_id)
        if incident is None:
            return None

        incident.active_alert_ids.discard(alert.id)
        incident.last_seen_at = alert.timestamp
        if not incident.active_alert_ids and incident.status != "resolved":
            incident.status = "resolved"
            incident.resolved_at = alert.resolved_at or alert.timestamp
            platform_metrics.increment("incident_resolved_total")
        if self._persistence is not None:
            self._persistence.enqueue(IncidentPersistenceEvent(incident=incident.snapshot()))
        return incident

    def acknowledge(self, incident_id: str) -> IncidentState | None:
        incident = self._incidents.get(incident_id)
        if incident is None or incident.status == "resolved":
            return None
        incident.status = "acknowledged"
        if self._persistence is not None:
            self._persistence.enqueue(
                IncidentPersistenceEvent(incident=incident.snapshot())
            )
        return incident

    def clear_host(self, host_id: str) -> None:
        ids = [key for key, item in self._incidents.items() if item.host_id == host_id]
        for incident_id in ids:
            self._incidents.pop(incident_id, None)
        self._alert_to_incident = {
            alert_id: incident_id
            for alert_id, incident_id in self._alert_to_incident.items()
            if incident_id in self._incidents
        }

    def get(self, incident_id: str) -> IncidentState | None:
        return self._incidents.get(incident_id)

    def all(self) -> list[IncidentState]:
        return sorted(self._incidents.values(), key=lambda item: item.last_seen_at, reverse=True)
