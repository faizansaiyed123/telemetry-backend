"""Tests for source-aware telemetry runtime behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.telemetry_manager import TelemetryManager


def test_source_mode_accepts_supported_values():
    for mode in ("synthetic", "agent", "hybrid"):
        manager = TelemetryManager(source_mode=mode)
        assert manager.source_mode == mode
        assert manager.simulation_enabled is (mode != "agent")


def test_source_mode_rejects_unknown_value():
    with pytest.raises(ValueError, match="source_mode must be one of"):
        TelemetryManager(source_mode="unknown")


@pytest.mark.asyncio
async def test_agent_mode_start_does_not_start_synthetic_generator(monkeypatch):
    manager = TelemetryManager(source_mode="agent")
    ensure_persistence = AsyncMock()
    manager._ensure_persistence = ensure_persistence
    manager.reload_alert_rules = MagicMock()

    db = MagicMock()
    db.scalars.return_value = []
    db.execute.return_value.all.return_value = []

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = db
    session_factory.return_value.__exit__.return_value = None
    monkeypatch.setattr("app.services.telemetry_manager.SessionLocal", session_factory)

    await manager.start()

    ensure_persistence.assert_awaited_once()
    manager.reload_alert_rules.assert_called_once_with(db)
    assert manager.running is False
    assert manager._task is None

    await manager.stop()


def test_agent_mode_trigger_is_blocked():
    manager = TelemetryManager(source_mode="agent")
    with pytest.raises(RuntimeError, match="disabled in agent source mode"):
        import asyncio
        asyncio.run(manager.trigger_anomaly("cpu"))
