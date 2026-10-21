"""Tests for the alert system (via TelemetryManager)."""

import pytest

from app.models.alerts import Severity
from app.services.telemetry_manager import TelemetryManager
from app.services.anomaly_detector import AnomalyDetector
from app.models.telemetry import TelemetryEvent
from app.utils.time import utc_now


def make_event(
    sequence: int = 0,
    cpu: float = 50.0,
    memory: float = 60.0,
    temperature: float = 50.0,
    network_mbps: float = 100.0,
    requests_per_second: int = 500,
    error_rate: float = 0.5,
    latency_ms: float = 30.0,
) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=utc_now(),
        sequence=sequence,
        cpu=cpu,
        memory=memory,
        temperature=temperature,
        network_mbps=network_mbps,
        requests_per_second=requests_per_second,
        error_rate=error_rate,
        latency_ms=latency_ms,
    )


class TestAlertSystem:
    @pytest.fixture
    def manager(self):
        return TelemetryManager(
            max_history_size=1000,
            telemetry_rate=10,
            max_rate=100,
            anomaly_threshold=3.0,
        )

    def test_no_alerts_initially(self, manager):
        assert manager.active_alert_count == 0
        assert manager.total_alert_count == 0
        assert manager.get_alerts() == []

    def test_alert_creation(self, manager):
        """An anomaly should create an alert."""
        for i in range(35):
            event = make_event(sequence=i, cpu=50.0)
            manager._sequence = i + 1
            manager._current = event
            manager._history.append(event)
            manager._events_generated += 1
            results = manager._anomaly_detector.update(event)
            manager._process_anomaly_results(results, event)

        event = make_event(sequence=35, cpu=99.0)
        manager._sequence = 36
        manager._current = event
        manager._history.append(event)
        manager._events_generated += 1
        results = manager._anomaly_detector.update(event)
        manager._process_anomaly_results(results, event)

        assert manager.active_alert_count >= 1
        alerts = manager.get_alerts()
        assert any(a.metric == "cpu" for a in alerts)

    def test_alert_deduplication(self, manager):
        """Continuous anomaly should not create duplicate alerts."""
        for i in range(35):
            event = make_event(sequence=i, cpu=50.0)
            manager._sequence = i + 1
            manager._current = event
            manager._history.append(event)
            manager._events_generated += 1
            results = manager._anomaly_detector.update(event)
            manager._process_anomaly_results(results, event)

        for i in range(35, 45):
            event = make_event(sequence=i, cpu=99.0)
            manager._sequence = i + 1
            manager._current = event
            manager._history.append(event)
            manager._events_generated += 1
            results = manager._anomaly_detector.update(event)
            manager._process_anomaly_results(results, event)

        cpu_alerts = [a for a in manager.get_alerts() if a.metric == "cpu" and not a.resolved]
        assert len(cpu_alerts) <= 1

    def test_alert_resolution(self, manager):
        """Alert should resolve when metric returns to normal."""
        for i in range(35):
            event = make_event(sequence=i, cpu=50.0)
            manager._sequence = i + 1
            manager._current = event
            manager._history.append(event)
            manager._events_generated += 1
            results = manager._anomaly_detector.update(event)
            manager._process_anomaly_results(results, event)

        event = make_event(sequence=35, cpu=99.0)
        manager._sequence = 36
        manager._current = event
        manager._history.append(event)
        manager._events_generated += 1
        results = manager._anomaly_detector.update(event)
        manager._process_anomaly_results(results, event)

        assert manager.active_alert_count >= 1

        event = make_event(sequence=36, cpu=50.0)
        manager._sequence = 37
        manager._current = event
        manager._history.append(event)
        manager._events_generated += 1
        results = manager._anomaly_detector.update(event)
        manager._process_anomaly_results(results, event)

        assert manager.active_alert_count == 0
        all_alerts = manager.get_alerts()
        assert any(a.resolved and a.metric == "cpu" for a in all_alerts)

    def test_alert_lifecycle_transitions(self, manager):
        """Alert should go: detected → active → resolved."""
        for i in range(35):
            event = make_event(sequence=i, cpu=50.0)
            manager._sequence = i + 1
            manager._current = event
            manager._history.append(event)
            manager._events_generated += 1
            results = manager._anomaly_detector.update(event)
            manager._process_anomaly_results(results, event)

        event = make_event(sequence=35, cpu=99.0)
        manager._sequence = 36
        manager._current = event
        manager._history.append(event)
        manager._events_generated += 1
        results = manager._anomaly_detector.update(event)
        manager._process_anomaly_results(results, event)
        assert manager.active_alert_count >= 1
        alert = manager.get_alerts()[0]
        assert not alert.resolved

        event = make_event(sequence=36, cpu=50.0)
        manager._sequence = 37
        manager._current = event
        manager._history.append(event)
        manager._events_generated += 1
        results = manager._anomaly_detector.update(event)
        manager._process_anomaly_results(results, event)
        assert manager.active_alert_count == 0

        resolved_alerts = manager.get_alerts()
        assert len(resolved_alerts) >= 1
        assert resolved_alerts[0].resolved is True
        assert resolved_alerts[0].resolved_at is not None

        assert manager.get_alerts(active_only=True) == []
