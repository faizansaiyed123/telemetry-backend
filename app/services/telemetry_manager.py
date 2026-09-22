"""Central telemetry runtime coordinating simulation, agent ingestion and alerts."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.alerts import Alert
from app.models.db import AlertRecord, Host, TelemetryRecord
from app.models.telemetry import TelemetryEvent, TelemetryStats
from app.services.aggregation import compute_stats
from app.services.alert_persistence import AlertPersistence
from app.services.anomaly_detector import AnomalyDetector, AnomalyResult
from app.services.telemetry_generator import TelemetryGenerator
from app.services.telemetry_persistence import TelemetryPersistence
from app.services.websocket_manager import WebSocketManager
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

GLOBAL_DETECTOR_KEY = "__global__"


class TelemetryManager:
    """Own live telemetry state and provide one pipeline for all telemetry sources."""

    def __init__(
        self,
        max_history_size: int = 5000,
        telemetry_rate: int = 10,
        max_rate: int = 100,
        anomaly_threshold: float = 3.0,
    ) -> None:
        self._generator = TelemetryGenerator()
        self._anomaly_threshold = anomaly_threshold
        self._anomaly_detector = AnomalyDetector(threshold=anomaly_threshold)
        self._anomaly_detectors: dict[str, AnomalyDetector] = {
            GLOBAL_DETECTOR_KEY: self._anomaly_detector
        }
        self._ws_manager = WebSocketManager()
        settings = get_settings()
        self._persistence: TelemetryPersistence | None = None
        self._alert_persistence: AlertPersistence | None = None
        self._persistence_host_id: str | None = None
        self._persistence_enabled = settings.telemetry_persistence_enabled
        self._persistence_host_name = settings.telemetry_host_name

        self._history: deque[TelemetryEvent] = deque(maxlen=max_history_size)
        self._max_history_size = max_history_size
        self._current: TelemetryEvent | None = None

        self._sequence: int = 0
        self._events_generated: int = 0
        self._events_ingested: int = 0

        self._running: bool = False
        self._rate: int = telemetry_rate
        self._max_rate: int = max_rate

        self._alerts: deque[Alert] = deque(maxlen=200)
        self._active_alerts: dict[object, Alert] = {}
        self._alert_id_counter: int = 0

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self._lock = asyncio.Lock()
        self._start_time: datetime | None = None

    async def _ensure_persistence(self) -> None:
        """Start the shared persistence workers when persistence is enabled."""
        if not self._persistence_enabled:
            return
        if self._persistence is not None and self._alert_persistence is not None:
            return

        telemetry_persistence: TelemetryPersistence | None = None
        alert_persistence: AlertPersistence | None = None
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
            await telemetry_persistence.start()
            await alert_persistence.start()
            self._persistence = telemetry_persistence
            self._alert_persistence = alert_persistence
        except Exception:
            logger.exception("Unable to initialize telemetry persistence; continuing in-memory")
            if telemetry_persistence is not None:
                await telemetry_persistence.stop()
            if alert_persistence is not None:
                await alert_persistence.stop()

    async def start(self) -> None:
        """Start the synthetic telemetry generation background task."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._start_time = utc_now()
        await self._ensure_persistence()
        self._task = asyncio.create_task(self._generation_loop())
        logger.info("Telemetry generation started at %d events/sec", self._rate)

    async def stop(self) -> None:
        """Stop generation and gracefully drain persistence workers."""
        if self._running or self._task is not None:
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
        logger.info("Telemetry generation stopped")

    @property
    def start_time(self) -> datetime | None:
        return self._start_time

    @property
    def ws_manager(self) -> WebSocketManager:
        return self._ws_manager

    async def pause(self) -> None:
        """Pause synthetic telemetry generation."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Telemetry generation paused")
        await self._broadcast_system("paused", "Telemetry generation paused")

    async def resume(self) -> None:
        """Resume synthetic telemetry generation."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        await self._ensure_persistence()
        self._task = asyncio.create_task(self._generation_loop())
        logger.info("Telemetry generation resumed at %d events/sec", self._rate)
        await self._broadcast_system("resumed", "Telemetry generation resumed")

    async def reset(self) -> None:
        """Reset synthetic telemetry state without deleting agent host history."""
        was_running = self._running
        synthetic_host_id = self._persistence_host_id
        await self.stop()

        if synthetic_host_id is not None:
            try:
                with SessionLocal() as db:
                    db.execute(delete(TelemetryRecord).where(TelemetryRecord.host_id == synthetic_host_id))
                    db.execute(delete(AlertRecord).where(AlertRecord.host_id == synthetic_host_id))
                    db.commit()
            except Exception:
                logger.exception("Unable to clear persisted synthetic telemetry state during reset")

        async with self._lock:
            self._history.clear()
            self._current = None
            self._sequence = 0
            self._events_generated = 0
            self._events_ingested = 0
            self._alerts.clear()
            self._active_alerts.clear()
            self._alert_id_counter = 0
            self._generator.reset()
            self._anomaly_detector.reset()
            self._anomaly_detectors = {GLOBAL_DETECTOR_KEY: self._anomaly_detector}
            self._start_time = utc_now() if was_running else self._start_time

        await self._ws_manager.disconnect_all()
        logger.info("Synthetic telemetry state reset")

        if was_running:
            await self.start()

        await self._broadcast_system("reset", "Synthetic telemetry state has been reset")

    async def set_rate(self, rate: int) -> None:
        """Set the synthetic telemetry generation rate."""
        if rate < 1 or rate > self._max_rate:
            raise ValueError(f"Rate must be between 1 and {self._max_rate}")
        old_rate = self._rate
        self._rate = rate
        logger.info("Telemetry rate changed from %d to %d events/sec", old_rate, rate)
        await self._broadcast_system("rate_changed", f"Telemetry rate changed to {rate}/sec")

    async def trigger_anomaly(self, metric: str, intensity: float = 1.0, duration_seconds: float = 3.0) -> None:
        """Trigger an anomaly in the synthetic source."""
        duration_events = max(1, int(duration_seconds * self._rate))
        self._generator.set_anomaly(metric, intensity=intensity, duration=duration_events)
        logger.info(
            "Anomaly triggered on metric: %s (intensity=%.1f, duration=%d events)",
            metric,
            intensity,
            duration_events,
        )
        await self._broadcast_system("anomaly_triggered", f"Anomaly triggered on {metric}")

    async def ingest_external(self, events: list[TelemetryEvent], host_id: str) -> tuple[int, int]:
        """Process agent telemetry through the same history, anomaly and broadcast pipeline."""
        if not events:
            return 0, 0

        broadcasts: list[Alert] = []
        async with self._lock:
            for event in events:
                normalized = event.model_copy(update={"host_id": host_id, "source": "agent"})
                self._current = normalized
                self._history.append(normalized)
                self._events_ingested += 1

                if self._persistence is not None:
                    self._persistence.enqueue(normalized, host_id)

                detector = self._get_detector(host_id)
                anomaly_results = detector.update(normalized)
                broadcasts.extend(self._process_anomaly_results(anomaly_results, normalized, host_id))

        for event in events:
            normalized = event.model_copy(update={"host_id": host_id, "source": "agent"})
            await self._broadcast_telemetry(normalized)

        for alert in broadcasts:
            await self._broadcast_alert(alert)

        return len(events), len(events) if self._persistence is not None else 0

    def _get_detector(self, host_id: str | None) -> AnomalyDetector:
        key = host_id or GLOBAL_DETECTOR_KEY
        detector = self._anomaly_detectors.get(key)
        if detector is None:
            detector = AnomalyDetector(threshold=self._anomaly_threshold)
            self._anomaly_detectors[key] = detector
        return detector

    @property
    def running(self) -> bool:
        return self._running

    @property
    def rate(self) -> int:
        return self._rate

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
    def connected_clients(self) -> int:
        return self._ws_manager.client_count

    @property
    def persistence_host_id(self) -> str | None:
        return self._persistence_host_id

    @property
    def persistence_queue_size(self) -> int:
        return self._persistence.queue_size if self._persistence is not None else 0

    @property
    def persistence_dropped_events(self) -> int:
        return self._persistence.dropped_events if self._persistence is not None else 0

    @property
    def persistence_persisted_events(self) -> int:
        return self._persistence.persisted_events if self._persistence is not None else 0

    @property
    def alert_persistence_queue_size(self) -> int:
        return self._alert_persistence.queue_size if self._alert_persistence is not None else 0

    @property
    def alert_persistence_dropped_events(self) -> int:
        return self._alert_persistence.dropped_events if self._alert_persistence is not None else 0

    def get_current(self) -> TelemetryEvent | None:
        return self._current

    def get_history(self, limit: int = 100) -> list[TelemetryEvent]:
        if limit < 1:
            limit = 1
        limit = min(limit, len(self._history))
        return list(self._history)[-limit:]

    def get_stats(self) -> TelemetryStats:
        return compute_stats(list(self._history))

    def get_alerts(self, active_only: bool = False) -> list[Alert]:
        active = list(self._active_alerts.values())
        if active_only:
            return active
        resolved = [alert for alert in self._alerts if alert.resolved]
        return active + resolved

    @property
    def active_alert_count(self) -> int:
        return len(self._active_alerts)

    @property
    def total_alert_count(self) -> int:
        return len(self._alerts)

    async def acknowledge_alert(self, alert_id: str) -> bool:
        async with self._lock:
            alert = next((item for item in self._alerts if item.id == alert_id), None)
            if alert is None:
                alert = next(
                    (item for item in self._active_alerts.values() if item.id == alert_id),
                    None,
                )
            if alert is None:
                return False

            if not alert.acknowledged:
                alert.acknowledged = True
                if self._alert_persistence is not None:
                    self._alert_persistence.enqueue(alert, alert.host_id)
            return True

    @property
    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (utc_now() - self._start_time).total_seconds()

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
            event = self._generator.generate(self._sequence)
            host_id = self._persistence_host_id
            event = event.model_copy(update={"host_id": host_id, "source": "simulation"})
            self._current = event
            self._history.append(event)
            self._events_generated += 1
            if self._persistence is not None:
                self._persistence.enqueue(event, host_id)

            detector = self._get_detector(host_id)
            anomaly_results = detector.update(event)
            alerts_to_broadcast = self._process_anomaly_results(anomaly_results, event, host_id)

        await self._broadcast_telemetry(event)
        for alert in alerts_to_broadcast:
            await self._broadcast_alert(alert)

    def _process_anomaly_results(
        self,
        results: list[AnomalyResult],
        event: TelemetryEvent,
        host_id: str | None = None,
    ) -> list[Alert]:
        broadcasts: list[Alert] = []
        for result in results:
            metric = result.metric
            key: object = (host_id or GLOBAL_DETECTOR_KEY, metric) if host_id else metric
            if result.is_anomaly:
                if key not in self._active_alerts:
                    self._alert_id_counter += 1
                    alert = Alert(
                        id=f"alert-{self._alert_id_counter}",
                        timestamp=event.timestamp,
                        host_id=host_id,
                        source="anomaly",
                        metric=metric,
                        value=result.value,
                        baseline=result.baseline,
                        severity=result.severity,
                        message=(
                            f"{metric} anomaly detected: {result.value} "
                            f"(baseline: {result.baseline}, z-score: {result.z_score})"
                        ),
                        resolved=False,
                    )
                    self._active_alerts[key] = alert
                    self._alerts.append(alert)
                    if self._alert_persistence is not None:
                        self._alert_persistence.enqueue(alert, host_id)
                    broadcasts.append(alert)
                    logger.warning(
                        "Alert created: %s = %s (z=%.2f, severity=%s, host=%s)",
                        metric,
                        result.value,
                        result.z_score,
                        result.severity,
                        host_id,
                    )
                else:
                    self._active_alerts[key].value = result.value
            else:
                if key in self._active_alerts:
                    alert = self._active_alerts.pop(key)
                    alert.resolved = True
                    alert.resolved_at = utc_now()
                    if self._alert_persistence is not None:
                        self._alert_persistence.enqueue(alert, host_id)
                    broadcasts.append(alert)
                    logger.info("Alert resolved: %s host=%s", metric, host_id)
        return broadcasts

    async def _broadcast_telemetry(self, event: TelemetryEvent) -> None:
        msg = json.dumps({"type": "telemetry", "data": event.model_dump(mode="json")})
        await self._ws_manager.broadcast(msg)

    async def _broadcast_alert(self, alert: Alert) -> None:
        await self._ws_manager.broadcast(
            json.dumps({"type": "alert", "data": alert.model_dump(mode="json")})
        )

    async def _broadcast_system(self, event: str, message: str) -> None:
        msg = json.dumps({"type": "system", "data": {"event": event, "message": message}})
        await self._ws_manager.broadcast(msg)
