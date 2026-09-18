"""Regression tests for telemetry manager lifecycle behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.alerts import Alert, Severity
from app.models.telemetry import TelemetryEvent
from app.services.anomaly_detector import AnomalyResult
from app.services.telemetry_manager import TelemetryManager
from app.utils.time import utc_now


def make_event() -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=utc_now(),
        sequence=1,
        cpu=50.0,
        memory=60.0,
        temperature=45.0,
        network_mbps=100.0,
        requests_per_second=500.0,
        error_rate=0.5,
        latency_ms=30.0,
    )



@pytest.mark.asyncio
async def test_start_initializes_persistence() -> None:
    manager = TelemetryManager()
    ensure_persistence = AsyncMock()
    manager._ensure_persistence = ensure_persistence

    try:
        await manager.start()
        ensure_persistence.assert_awaited_once()
        assert manager._task is not None
    finally:
        await manager.stop()

@pytest.mark.asyncio
async def test_stop_closes_both_persistence_workers() -> None:
    manager = TelemetryManager()
    telemetry_persistence = AsyncMock()
    alert_persistence = AsyncMock()
    manager._persistence = telemetry_persistence
    manager._alert_persistence = alert_persistence

    await manager.stop()

    telemetry_persistence.stop.assert_awaited_once()
    alert_persistence.stop.assert_awaited_once()
    assert manager._persistence is None
    assert manager._alert_persistence is None


@pytest.mark.asyncio
async def test_resume_restores_persistence_after_stop() -> None:
    manager = TelemetryManager()
    manager._persistence_enabled = True
    manager._persistence_host_id = "host-1"
    manager._persistence = AsyncMock()
    manager._alert_persistence = AsyncMock()

    await manager.stop()
    assert manager._persistence is None
    assert manager._alert_persistence is None

    ensure_persistence = AsyncMock()
    manager._ensure_persistence = ensure_persistence

    try:
        await manager.resume()
        ensure_persistence.assert_awaited_once()
        assert manager._task is not None
    finally:
        await manager.stop()


def test_resolving_alert_is_persisted() -> None:
    manager = TelemetryManager()
    persistence = MagicMock()
    manager._alert_persistence = persistence
    manager._persistence_host_id = "host-1"

    alert = Alert(
        id="alert-1",
        timestamp=utc_now(),
        metric="cpu",
        value=99.0,
        baseline=50.0,
        severity=Severity.CRITICAL,
        message="CPU anomaly",
    )
    manager._active_alerts["cpu"] = alert
    manager._alerts.append(alert)

    result = AnomalyResult(
        metric="cpu",
        value=50.0,
        baseline=50.0,
        z_score=0.0,
        is_anomaly=False,
        severity=Severity.INFO,
    )
    broadcasts = manager._process_anomaly_results([result], make_event())

    assert broadcasts[0].resolved is True
    persistence.enqueue.assert_called_once()
    persisted_alert, host_id = persistence.enqueue.call_args.args
    assert persisted_alert.id == "alert-1"
    assert persisted_alert.resolved is True
    assert host_id == "host-1"
