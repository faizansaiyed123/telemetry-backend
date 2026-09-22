"""Central telemetry runtime, anomaly detection, alerting, and ingestion orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.alerts import Alert
from app.models.db import AlertRecord, AlertRule, Host, Incident, TelemetryRecord
from app.models.telemetry import TelemetryEvent, TelemetryStats
from app.services.aggregation import compute_stats
from app.services.alert_persistence import AlertPersistence
from app.services.alert_rule_engine import AlertRuleEngine, RuleTransition
from app.services.anomaly_detector import AnomalyDetector, AnomalyResult
from app.services.incident_engine import IncidentEngine, IncidentPersistence
from app.services.platform_metrics import platform_metrics
from app.services.telemetry_generator import TelemetryGenerator
from app.services.telemetry_persistence import TelemetryPersistence
from app.services.websocket_manager import WebSocketManager
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class TelemetryManager:
    """Single-process runtime coordinator for all telemetry sources."""

    def __init__(
        self,
        max_history_size: int = 5000,
        telemetry_rate: int = 10,
        max_rate: int = 100,
        anomaly_threshold: float = 3.0,
    ) -> None:
        settings = get_settings()
        self._generator = TelemetryGenerator()
        self._anomaly_detector = AnomalyDetector(threshold=anomaly_threshold)
        self._alert_rule_engine = AlertRuleEngine()
        self._ws_manager = WebSocketManager()
        self._history: deque[TelemetryEvent] = deque(maxlen=max_history_size)
        self._current: TelemetryEvent | None = None
        self._current_by_host: dict[str, TelemetryEvent] = {}
        self._max_history_size = max_history_size
        self._sequence = 0
        self._events_generated = 0
        self._events_ingested = 0
        self._running = False
        self._rate = telemetry_rate
        self._max_rate = max_rate
        self._alerts: deque[Alert] = deque(maxlen=500)
        self._active_alerts: dict[str, Alert] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._start_time: datetime | None = None
        self._persistence_enabled = settings.telemetry_persistence_enabled
        self._persistence_host_name = settings.telemetry_host_name
        self._persistence_host_id: str | None = None
        self._persistence: TelemetryPersistence | None = None
        self._alert_persistence: AlertPersistence | None = None
        self._incident_persistence: IncidentPersistence | None = None
        self._incident_engine = IncidentEngine()
        self._rule_count = 0

    # --- Lifecycle ---

    async def _ensure_persistence(self) -> None:
        if not self._persistence_enabled:
            return
        if self._persistence is not None and self._alert_persistence is not None:
            return

        telemetry_persistence: TelemetryPersistence | None = None
        alert_persistence: AlertPersistence | None = None
        incident_persistence: IncidentPersistence | None = None
        try:
            with SessionLocal() as db:
                host = None
                if self._persistence_host_id is not None:
                    host = db.scalar(
                        select(Host).where(
                            Host.id == self._persistence_host_id,
                            Host.is_active.is_(True),
                        )
                    )
                if host is None:
                    host = db.scalar(
                        select(Host).where(
                            Host.name == self._persistence_host_name,
                            Host.is_active.is_(True),
                        )
                    )
                if host is None:
                    logger.warning(
                        "Telemetry persistence unavailable: host %r was not found",
                        self._persistence_host_name,
                    )
                    return
                self._persistence_host_id = host.id

            telemetry_persistence = TelemetryPersistence(host.id)
            alert_persistence = AlertPersistence()
            incident_persistence = IncidentPersistence()
            await telemetry_persistence.start()
            await alert_persistence.start()
            await incident_persistence.start()
            self._persistence = telemetry_persistence
            self._alert_persistence = alert_persistence
            self._incident_persistence = incident_persistence
            self._incident_engine = IncidentEngine(incident_persistence)
            await self._incident_engine.start()
        except Exception:
            logger.exception("Unable to initialize persistence workers; continuing in memory")
            for worker in (telemetry_persistence, alert_persistence, incident_persistence):
                if worker is not None:
                    await worker.stop()

    def reload_alert_rules(self, db: Session) -> None:
        """Reload persisted rules after an administrative mutation."""
        rules = list(db.scalars(select(AlertRule).order_by(AlertRule.name)))
        self._rule_count = len(rules)
        self._alert_rule_engine.load_rules(rules)
        active_rule_alerts = [
            alert
            for alert in self._alerts
            if alert.source == "rule" and not alert.resolved and alert.rule_id
        ]
        self._alert_rule_engine.hydrate_active_alerts(active_rule_alerts)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._start_time = utc_now()
        await self._ensure_persistence()
        try:
            with SessionLocal() as db:
                self.reload_alert_rules(db)
                if self._persistence_host_id is not None:
                    active_rows = list(
                        db.scalars(
                            select(AlertRecord).where(
                                AlertRecord.host_id == self._persistence_host_id,
                                AlertRecord.status == "active",
                                AlertRecord.source == "rule",
                            )
                        )
                    )
                    hydrated: list[Alert] = []
                    for row in active_rows:
                        alert = Alert(
                            id=row.id,
                            timestamp=row.timestamp,
                            metric=row.metric,
                            value=row.value,
                            baseline=row.baseline,
                            severity=row.severity,
                            message=row.message,
                            acknowledged=row.acknowledged,
                            host_id=row.host_id,
                            source=row.source,
                            rule_id=row.rule_id,
                        )
                        hydrated.append(alert)
                        key = f"rule:{row.rule_id}:{row.host_id or 'system'}"
                        self._alerts.append(alert)
                        self._active_alerts[key] = alert
                    self._alert_rule_engine.hydrate_active_alerts(hydrated)
        except Exception:
            logger.exception("Unable to load persisted alert rules/state")
        self._task = asyncio.create_task(self._generation_loop())
        logger.info("Telemetry generation started at %d events/sec", self._rate)

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._persistence is not None:
            await self._persistence.stop()
            self._persistence = None
        if self._alert_persistence is not None:
            await self._alert_persistence.stop()
            self._alert_persistence = None
        if self._incident_engine is not None:
            await self._incident_engine.stop()
        self._incident_persistence = None
        platform_metrics.set_gauge("telemetry_persistence_queue_depth", 0)
        platform_metrics.set_gauge("alert_persistence_queue_depth", 0)
        platform_metrics.set_gauge("incident_persistence_queue_depth", 0)
        logger.info("Telemetry generation stopped")

    async def pause(self) -> None:
        if not self._running:
            return
        await self.stop()
        await self._broadcast_system("paused", "Telemetry generation paused")

    async def resume(self) -> None:
        if self._running:
            return
        await self.start()
        await self._broadcast_system("resumed", "Telemetry generation resumed")

    async def reset(self) -> None:
        was_running = self._running
        persistence_host_id = self._persistence_host_id
        await self.stop()

        if persistence_host_id is not None:
            try:
                with SessionLocal() as db:
                    db.execute(delete(TelemetryRecord).where(TelemetryRecord.host_id == persistence_host_id))
                    db.execute(delete(AlertRecord).where(AlertRecord.host_id == persistence_host_id))
                    db.execute(delete(Incident).where(Incident.host_id == persistence_host_id))
                    db.commit()
            except Exception:
                logger.exception("Unable to clear persisted state during reset")

        async with self._lock:
            self._history.clear()
            self._current = None
            self._current_by_host.clear()
            self._sequence = 0
            self._events_generated = 0
            self._events_ingested = 0
            self._alerts.clear()
            self._active_alerts.clear()
            self._generator.reset()
            self._anomaly_detector.reset()
            self._alert_rule_engine.reset()
            self._incident_engine = IncidentEngine()
            self._start_time = utc_now() if was_running else self._start_time

        await self._ws_manager.disconnect_all()
        logger.info("Telemetry state reset")
        if was_running:
            await self.start()
        await self._broadcast_system("reset", "Telemetry state has been reset")

    async def set_rate(self, rate: int) -> None:
        if rate < 1 or rate > self._max_rate:
            raise ValueError(f"Rate must be between 1 and {self._max_rate}")
        old_rate = self._rate
        self._rate = rate
        logger.info("Telemetry rate changed from %d to %d events/sec", old_rate, rate)
        await self._broadcast_system("rate_changed", f"Telemetry rate changed to {rate}/sec")

    async def trigger_anomaly(
        self,
        metric: str,
        intensity: float = 1.0,
        duration_seconds: float = 3.0,
    ) -> None:
        duration_events = max(1, int(duration_seconds * self._rate))
        self._generator.set_anomaly(metric, intensity=intensity, duration=duration_events)
        logger.info(
            "Telemetry anomaly triggered metric=%s intensity=%.1f duration_events=%d",
            metric,
            intensity,
            duration_events,
        )
        await self._broadcast_system("anomaly_triggered", f"Anomaly triggered on {metric}")

    # --- Event processing ---

    async def process_event(self, event: TelemetryEvent, *, persist: bool = True) -> None:
        """Send any telemetry source through the same processing pipeline."""
        alerts_to_broadcast: list[Alert] = []
        incident_events: list[tuple[str, str]] = []

        async with self._lock:
            self._current = event
            self._history.append(event)
            if event.host_id:
                self._current_by_host[event.host_id] = event

            if event.source == "synthetic":
                self._events_generated += 1
            else:
                self._events_ingested += 1

            if persist and self._persistence is not None:
                self._persistence.enqueue(event)

            anomaly_results = self._anomaly_detector.update(event)
            alerts_to_broadcast.extend(self._process_anomaly_results(anomaly_results, event))

            for transition in self._alert_rule_engine.evaluate(event):
                alert = self._apply_rule_transition(transition)
                if alert is not None:
                    alerts_to_broadcast.append(alert)
                    incident_events.append(
                        ("created" if transition.action == "created" else "resolved", alert.id)
                    )

        await self._broadcast_telemetry(event)
        for alert in alerts_to_broadcast:
            await self._broadcast_alert(alert)
        for action, alert_id in incident_events:
            await self._broadcast_system(
                "incident_updated",
                f"Incident state changed for alert {alert_id}: {action}",
            )

        platform_metrics.increment(
            "telemetry_generated_total" if event.source == "synthetic" else "telemetry_ingested_total"
        )
        platform_metrics.set_gauge("websocket_clients", self.connected_clients)
        self._update_persistence_metrics()

    def _apply_rule_transition(self, transition: RuleTransition) -> Alert | None:
        alert = transition.alert
        key = f"rule:{alert.rule_id}:{alert.host_id or 'system'}"
        if transition.action == "created":
            self._alerts.append(alert)
            self._active_alerts[key] = alert
            platform_metrics.increment("alert_created_total")
            incident = self._incident_engine.on_alert_created(alert)
            alert.incident_id = incident.id
            if self._alert_persistence is not None:
                self._alert_persistence.enqueue(alert, alert.host_id)
            return alert

        existing = self._active_alerts.pop(key, None)
        if existing is None:
            return alert

        existing.value = alert.value
        existing.message = alert.message
        existing.resolved = True
        existing.resolved_at = alert.resolved_at
        platform_metrics.increment("alert_resolved_total")
        self._incident_engine.on_alert_resolved(existing)
        if self._alert_persistence is not None:
            self._alert_persistence.enqueue(existing, existing.host_id)
        return existing

    def _process_anomaly_results(
        self,
        results: list[AnomalyResult],
        event: TelemetryEvent,
    ) -> list[Alert]:
        broadcasts: list[Alert] = []
        host_key = event.host_id or "system"

        for result in results:
            metric = result.metric
            key = f"anomaly:{host_key}:{metric}"

            if result.is_anomaly:
                if key not in self._active_alerts:
                    alert = Alert(
                        id=uuid4().hex,
                        timestamp=event.timestamp,
                        metric=metric,
                        value=result.value,
                        baseline=result.baseline,
                        severity=result.severity,
                        message=(
                            f"{metric} anomaly detected: {result.value} "
                            f"(baseline: {result.baseline}, z-score: {result.z_score})"
                        ),
                        host_id=event.host_id,
                        source="anomaly",
                    )
                    self._active_alerts[key] = alert
                    self._alerts.append(alert)
                    platform_metrics.increment("alert_created_total")
                    incident = self._incident_engine.on_alert_created(alert)
                    alert.incident_id = incident.id
                    if self._alert_persistence is not None:
                        self._alert_persistence.enqueue(alert, event.host_id)
                    broadcasts.append(alert)
                else:
                    self._active_alerts[key].value = result.value
            else:
                existing = self._active_alerts.pop(key, None)
                if existing is not None:
                    existing.resolved = True
                    existing.resolved_at = utc_now()
                    platform_metrics.increment("alert_resolved_total")
                    self._incident_engine.on_alert_resolved(existing)
                    if self._alert_persistence is not None:
                        self._alert_persistence.enqueue(existing, event.host_id)
                    broadcasts.append(existing)

        return broadcasts

    async def _broadcast_telemetry(self, event: TelemetryEvent) -> None:
        message = json.dumps({"type": "telemetry", "data": event.model_dump(mode="json")})
        await self._ws_manager.broadcast(message)

    async def _broadcast_alert(self, alert: Alert) -> None:
        await self._ws_manager.broadcast(
            json.dumps({"type": "alert", "data": alert.model_dump(mode="json")})
        )

    async def _broadcast_system(self, event: str, message: str) -> None:
        await self._ws_manager.broadcast(
            json.dumps({"type": "system", "data": {"event": event, "message": message}})
        )

    # --- State/query helpers ---

    @property
    def running(self) -> bool:
        return self._running

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def max_history_size(self) -> int:
        return self._max_history_size

    @property
    def max_rate(self) -> int:
        return self._max_rate

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def events_generated(self) -> int:
        return self._events_generated

    @property
    def events_ingested(self) -> int:
        return self._events_ingested

    @property
    def active_anomaly(self) -> str | None:
        return self._generator.active_anomaly

    @property
    def ws_manager(self) -> WebSocketManager:
        """Expose the WebSocket manager for integration and lifecycle callers."""
        return self._ws_manager

    @property
    def connected_clients(self) -> int:
        return self._ws_manager.client_count

    @property
    def persistence_host_id(self) -> str | None:
        return self._persistence_host_id

    @property
    def incident_engine(self) -> IncidentEngine:
        return self._incident_engine

    @property
    def rule_count(self) -> int:
        return self._rule_count

    @property
    def rule_evaluations(self) -> int:
        return self._alert_rule_engine.evaluations

    @property
    def rule_fires(self) -> int:
        return self._alert_rule_engine.fires

    @property
    def rule_resolutions(self) -> int:
        return self._alert_rule_engine.resolutions

    @property
    def active_alert_count(self) -> int:
        return len(self._active_alerts)

    @property
    def total_alert_count(self) -> int:
        return len(self._alerts)

    @property
    def telemetry_persistence_queue(self) -> int:
        return self._persistence.queue_depth if self._persistence is not None else 0

    @property
    def telemetry_persisted_events(self) -> int:
        return self._persistence.persisted_events if self._persistence is not None else 0

    @property
    def telemetry_dropped_events(self) -> int:
        return self._persistence.dropped_events if self._persistence is not None else 0

    @property
    def alert_persistence_queue(self) -> int:
        return self._alert_persistence.queue_depth if self._alert_persistence is not None else 0

    @property
    def alert_persisted_events(self) -> int:
        return self._alert_persistence.persisted_events if self._alert_persistence is not None else 0

    @property
    def alert_dropped_events(self) -> int:
        return self._alert_persistence.dropped_events if self._alert_persistence is not None else 0

    @property
    def incident_persistence_queue(self) -> int:
        return self._incident_persistence.queue_depth if self._incident_persistence is not None else 0

    def get_current(self, host_id: str | None = None) -> TelemetryEvent | None:
        if host_id is None:
            return self._current
        return self._current_by_host.get(host_id)

    def get_history(self, limit: int = 100, host_id: str | None = None) -> list[TelemetryEvent]:
        safe_limit = max(1, min(limit, self._max_history_size))
        if host_id is None:
            return list(self._history)[-safe_limit:]
        filtered = [item for item in self._history if item.host_id == host_id]
        return filtered[-safe_limit:]

    def get_stats(self, host_id: str | None = None) -> TelemetryStats:
        return compute_stats(self.get_history(self._max_history_size, host_id=host_id))

    def get_alerts(self, active_only: bool = False) -> list[Alert]:
        active = list(self._active_alerts.values())
        if active_only:
            return active
        resolved = [alert for alert in self._alerts if alert.resolved]
        return active + resolved

    async def acknowledge_alert(self, alert_id: str) -> bool:
        async with self._lock:
            alert = next((item for item in self._alerts if item.id == alert_id), None)
            if alert is None:
                alert = next((item for item in self._active_alerts.values() if item.id == alert_id), None)
            if alert is None:
                return False
            if alert.acknowledged:
                return True
            alert.acknowledged = True
            if self._alert_persistence is not None:
                self._alert_persistence.enqueue(alert, alert.host_id)
            return True

    @property
    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (utc_now() - self._start_time).total_seconds()

    def runtime_metrics(self) -> dict[str, int | float | bool | None]:
        self._update_persistence_metrics()
        return {
            "running": self.running,
            "rate": self.rate,
            "sequence": self.sequence,
            "events_generated": self.events_generated,
            "events_ingested": self.events_ingested,
            "connected_clients": self.connected_clients,
            "active_alerts": self.active_alert_count,
            "total_alerts": self.total_alert_count,
            "rule_count": self.rule_count,
            "rule_evaluations": self.rule_evaluations,
            "rule_fires": self.rule_fires,
            "rule_resolutions": self.rule_resolutions,
            "telemetry_persistence_queue": self.telemetry_persistence_queue,
            "telemetry_persisted_events": self.telemetry_persisted_events,
            "telemetry_dropped_events": self.telemetry_dropped_events,
            "alert_persistence_queue": self.alert_persistence_queue,
            "alert_persisted_events": self.alert_persisted_events,
            "alert_dropped_events": self.alert_dropped_events,
            "incident_persistence_queue": self.incident_persistence_queue,
            "open_incidents": sum(1 for item in self._incident_engine.all() if item.status != "resolved"),
        }

    def _update_persistence_metrics(self) -> None:
        platform_metrics.set_gauge("websocket_clients", self.connected_clients)
        platform_metrics.set_gauge("telemetry_persistence_queue_depth", self.telemetry_persistence_queue)
        platform_metrics.set_gauge("alert_persistence_queue_depth", self.alert_persistence_queue)
        platform_metrics.set_gauge("incident_persistence_queue_depth", self.incident_persistence_queue)
        platform_metrics.set_gauge(
            "open_incidents",
            sum(1 for item in self._incident_engine.all() if item.status != "resolved"),
        )

    # --- Generation ---

    async def _generation_loop(self) -> None:
        logger.debug("Generation loop started")
        while self._running and not self._stop_event.is_set():
            try:
                await self._generate_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected error in generation loop")

            interval = 1.0 / self._rate
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
        logger.debug("Generation loop ended")

    async def _generate_one(self) -> None:
        async with self._lock:
            self._sequence += 1
            raw_event = self._generator.generate(self._sequence)
            event = raw_event.model_copy(
                update={
                    "host_id": self._persistence_host_id,
                    "source": "synthetic",
                }
            )
        await self.process_event(event, persist=True)
