"""System status models."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    uptime_seconds: float
    stream_active: bool
    connected_clients: int
    events_generated: int


class ReadinessResponse(BaseModel):
    """Readiness probe response."""

    status: str
    database_connected: bool
    telemetry_manager_available: bool


class SimulationStatus(BaseModel):
    """Current simulation state."""

    running: bool
    rate: int
    sequence: int
    events_generated: int
    active_anomaly: str | None
    connected_clients: int
    uptime_seconds: float
