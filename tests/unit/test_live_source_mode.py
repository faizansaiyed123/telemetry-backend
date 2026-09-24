"""Tests for source-aware telemetry runtime behavior."""

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
async def test_agent_mode_start_does_not_start_synthetic_generator():
    manager = TelemetryManager(source_mode="agent")
    manager._ensure_persistence = pytest.AsyncMock() if hasattr(pytest, "AsyncMock") else None
