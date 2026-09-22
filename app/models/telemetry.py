"""Telemetry data models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TelemetryEvent(BaseModel):
    """A single telemetry event representing a point-in-time system snapshot."""

    timestamp: datetime
    sequence: int = Field(ge=0, description="Source sequence number; monotonic per host/source")
    cpu: float = Field(ge=0, le=100, description="CPU usage percentage")
    memory: float = Field(ge=0, le=100, description="Memory usage percentage")
    temperature: float = Field(ge=0, le=120, description="Temperature in Celsius")
    network_mbps: float = Field(ge=0, le=10000, description="Network throughput in Mbps")
    requests_per_second: float = Field(ge=0, le=1000000, description="Requests per second")
    error_rate: float = Field(ge=0, le=100, description="Error rate percentage")
    latency_ms: float = Field(ge=0, le=10000, description="Latency in milliseconds")
    host_id: str | None = Field(default=None, description="Host that produced the event")
    source: str = Field(default="synthetic", pattern=r"^(synthetic|agent|api)$")
    agent_version: str | None = None

    @field_validator("cpu", "memory", "error_rate")
    @classmethod
    def validate_percentages(cls, v: float) -> float:
        if v < 0 or v > 100:
            raise ValueError("percentage must be between 0 and 100")
        return v


class TelemetryStats(BaseModel):
    """Aggregated statistics over a set of telemetry events."""

    count: int = Field(ge=0)
    cpu: dict[str, float | None] = Field(description="min, max, avg, latest for CPU")
    memory: dict[str, float | None] = Field(description="min, max, avg, latest for memory")
    temperature: dict[str, float | None] = Field(description="min, max, avg, latest for temperature")
    network_mbps: dict[str, float | None] = Field(description="min, max, avg, latest for network")
    requests_per_second: dict[str, float | None] = Field(description="min, max, avg, latest for rps")
    error_rate: dict[str, float | None] = Field(description="min, max, avg, latest for error rate")
    latency_ms: dict[str, float | None] = Field(description="min, max, avg, latest for latency")


class HistoryResponse(BaseModel):
    """Response for the history endpoint."""

    events: list[TelemetryEvent]
    count: int
    limit: int


class CurrentTelemetryResponse(BaseModel):
    """Response for the current telemetry endpoint."""

    event: TelemetryEvent | None = None
    available: bool
