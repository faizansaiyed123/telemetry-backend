"""Telemetry manager — owns all telemetry state.

Central hub between the generator and all consumers (WebSocket, REST).
Owns: current telemetry, bounded history, sequence number, simulation state,
event publication, generation counters, rate state, anomaly state, alerts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime

from app.core.config import get_settings
from app.models.alerts import Alert
from app.models.telemetry import TelemetryEvent, TelemetryStats
from app.services.aggregation import compute_stats
from app.services.anomaly_detector import AnomalyDetector, AnomalyResult
from app.services.telemetry_generator import TelemetryGenerator
from app.services.telemetry_persistence import TelemetryPersistence
from app.services.websocket_manager import WebSocketManager
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class TelemetryManager:
    """Central telemetry manager owning all state and the generation loop."""

    def __init__(
        self,
        max_history_size: int = 5000,
        telemetry_rate: int = 10,
        max_rate: int = 100,
        anomaly_threshold: float = 3.0,
    ) -> None:
        self._generator = TelemetryGenerator()
        self._anomaly_detector = AnomalyDetector(threshold=anomaly_threshold)
        self._ws_manager = WebSocketManager()
        settings = get_settings()
        self._persistence: TelemetryPersistence | None = None
        self._persistence_enabled = settings.telemetry_persistence_enabled
        self._persistence_host_name = settings.telemetry_host_name

        self._history: deque[TelemetryEvent] = deque(maxlen=max_history_size)
        self._max_history_size = max_history_size
        self._current: TelemetryEvent | None = None

        self._sequence: int = 0
        self._events_generated: int = 0

        self._running: bool = False
        self._rate: int = telemetry_rate
        self._max_rate: int = max_rate

        self._alerts: deque[Alert] = deque(maxlen=200)
        self._active_alerts: dict[str, Alert] = {}  # metric -> active alert
        self._alert_id_counter: int = 0

        self._task: asyncio.Task | None = None
        self._persistence_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self._lock = asyncio.Lock()
        self._start_time: datetime | None = None

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the telemetry generation background task."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._start_time = utc_now()
        if self._persistence_enabled:
            from sqlalchemy import select
            from app.db.session import SessionLocal
            from app.models.db import Host
            try:
                with SessionLocal() as db:
                    host = db.scalar(select(Host).where(Host.name == self._persistence_host_name, Host.is_active.is_(True)))
                    if host is not None:
                        self._persistence = TelemetryPersistence(host.id)
                        await self._persistence.start()
                    else:
                        logger.warning("Telemetry persistence disabled for this run: host %r was not found", self._persistence_host_name)
            except Exception:
                logger.exception("Unable to initialize telemetry persistence; continuing in-memory")
        self._task = asyncio.create_task(self._generation_loop())
        logger.info("Telemetry generation started at %d events/sec", self._rate)

    async def stop(self) -> None:
        """Stop the telemetry generation background task gracefully."""
        if not self._running and self._task is None:
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
        if self._persistence is not None:
            await self._persistence.stop()
            self._persistence = None
        logger.info("Telemetry generation stopped")

    @property
    def start_time(self) -> datetime | None:
        return self._start_time

    @property
    def ws_manager(self) -> WebSocketManager:
        return self._ws_manager

    # --- Simulation controls ---

    async def pause(self) -> None:
        """Pause telemetry generation without stopping the task."""
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
        """Resume telemetry generation."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._generation_loop())
        logger.info("Telemetry generation resumed at %d events/sec", self._rate)
        await self._broadcast_system("resumed", "Telemetry generation resumed")

    async def reset(self) -> None:
        """Reset all telemetry state.

        Clears: history, current telemetry, sequence, alerts, anomaly state,
        generator state, anomaly detector history. Disconnects all WebSocket clients.
        """
        was_running = self._running
        await self.stop()

        async with self._lock:
            self._history.clear()
            self._current = None
            self._sequence = 0
            self._events_generated = 0
            self._alerts.clear()
            self._active_alerts.clear()
            self._alert_id_counter = 0
            self._generator.reset()
            self._anomaly_detector.reset()
            self._start_time = utc_now() if was_running else self._start_time

        await self._ws_manager.disconnect_all()
        logger.info("Telemetry state reset")
        await self._broadcast_system("reset", "Telemetry state has been reset")

    async def set_rate(self, rate: int) -> None:
        """Set the telemetry rate (events per second)."""
        if rate < 1 or rate > self._max_rate:
            raise ValueError(f"Rate must be between 1 and {self._max_rate}")
        old_rate = self._rate
        self._rate = rate
        logger.info("Telemetry rate changed from %d to %d events/sec", old_rate, rate)
        await self._broadcast_system("rate_changed", f"Telemetry rate changed to {rate}/sec")

    async def trigger_anomaly(self, metric: str, intensity: float = 1.0, duration_seconds: float = 3.0) -> None:
        """Trigger an anomaly on the specified metric."""
        duration_events = max(1, int(duration_seconds * self._rate))
        self._generator.set_anomaly(metric, intensity=intensity, duration=duration_events)
        logger.info("Anomaly triggered on metric: %s (intensity=%.1f, duration=%d events)", metric, intensity, duration_events)
        await self._broadcast_system("anomaly_triggered", f"Anomaly triggered on {metric}")

    # --- State queries ---

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
    def active_anomaly(self) -> str | None:
        return self._generator.active_anomaly

    @property
    def connected_clients(self) -> int:
        return self._ws_manager.client_count

    def get_current(self) -> TelemetryEvent | None:
        return self._current

    def get_history(self, limit: int = 100) -> list[TelemetryEvent]:
        """Return the most recent `limit` events in chronological order."""
        if limit < 1:
            limit = 1
        if limit > len(self._history):
            limit = len(self._history)
        # deque stores in chronological order; take the last `limit` items
        items = list(self._history)[-limit:]
        return items

    def get_stats(self) -> TelemetryStats:
        """Compute and return statistics over the full history."""
        return compute_stats(list(self._history))

    def get_alerts(self, active_only: bool = False) -> list[Alert]:
        """Return alerts (active first then resolved, or active only)."""
        active = [a for a in self._active_alerts.values()]
        if active_only:
            return active
        resolved = [a for a in self._alerts if a.resolved]
        return active + resolved

    @property
    def active_alert_count(self) -> int:
        return len(self._active_alerts)

    @property
    def total_alert_count(self) -> int:
        return len(self._alerts)

    @property
    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (utc_now() - self._start_time).total_seconds()

    # --- Generation loop ---

    async def _generation_loop(self) -> None:
        """Background task that generates telemetry at the configured rate."""
        logger.debug("Generation loop started")
        while self._running and not self._stop_event.is_set():
            try:
                await self._generate_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected error in generation loop")

            # Sleep for the interval, checking stop_event for faster cancellation
            interval = 1.0 / self._rate
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                # If we get here, stop_event was set
                break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
        logger.debug("Generation loop ended")

    async def _generate_one(self) -> None:
        """Generate a single telemetry event, update state, check anomalies, broadcast."""
        async with self._lock:
            self._sequence += 1
            event = self._generator.generate(self._sequence)
            self._current = event
            self._history.append(event)
            self._events_generated += 1
            if self._persistence is not None:
                self._persistence.enqueue(event)

            # Check for anomalies
            anomaly_results = self._anomaly_detector.update(event)
            alerts_to_broadcast = self._process_anomaly_results(anomaly_results, event)

        # Broadcast outside the lock
        msg = json.dumps({"type": "telemetry", "data": event.model_dump(mode="json")})
        await self._ws_manager.broadcast(msg)

        for alert in alerts_to_broadcast:
            alert_msg = json.dumps({"type": "alert", "data": alert.model_dump(mode="json")})
            await self._ws_manager.broadcast(alert_msg)

    def _process_anomaly_results(
        self, results: list[AnomalyResult], event: TelemetryEvent
    ) -> list[Alert]:
        """Process anomaly results and manage alert lifecycle.

        Returns list of new or resolved alerts to broadcast.
        """
        broadcasts: list[Alert] = []

        for result in results:
            metric = result.metric
            if result.is_anomaly:
                # Create alert if not already active (deduplication)
                if metric not in self._active_alerts:
                    self._alert_id_counter += 1
                    alert = Alert(
                        id=f"alert-{self._alert_id_counter}",
                        timestamp=event.timestamp,
                        metric=metric,
                        value=result.value,
                        baseline=result.baseline,
                        severity=result.severity,
                        message=f"{metric} anomaly detected: {result.value} (baseline: {result.baseline}, z-score: {result.z_score})",
                        resolved=False,
                    )
                    self._active_alerts[metric] = alert
                    self._alerts.append(alert)
                    broadcasts.append(alert)
                    logger.warning(
                        "Alert created: %s = %s (z=%.2f, severity=%s)",
                        metric,
                        result.value,
                        result.z_score,
                        result.severity,
                    )
                else:
                    # Update existing alert value
                    self._active_alerts[metric].value = result.value
            else:
                # Resolve alert if it exists
                if metric in self._active_alerts:
                    alert = self._active_alerts.pop(metric)
                    alert.resolved = True
                    alert.resolved_at = utc_now()
                    broadcasts.append(alert)
                    logger.info("Alert resolved: %s", metric)

        return broadcasts

    async def _broadcast_system(self, event: str, message: str) -> None:
        """Broadcast a system message to all WebSocket clients."""
        msg = json.dumps({"type": "system", "data": {"event": event, "message": message}})
        await self._ws_manager.broadcast(msg)
