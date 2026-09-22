"""Alert models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Alert severity levels."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Alert(BaseModel):
    """A single alert representing a detected anomaly or rule violation."""

    id: str
    timestamp: datetime
    metric: str
    value: float
    baseline: float
    severity: Severity
    message: str
    resolved: bool = False
    resolved_at: datetime | None = None
    acknowledged: bool = False
    host_id: str | None = None
    source: str = "anomaly"
    rule_id: str | None = None
    incident_id: str | None = None


class AlertsResponse(BaseModel):
    """Response for the alerts endpoint."""

    alerts: list[Alert]
    active_count: int
    total_count: int


class TriggerAnomalyRequest(BaseModel):
    """Request body for triggering an anomaly."""

    metric: str = Field(
        ...,
        description="Metric to inject anomaly into: cpu, memory, temperature, latency, error_rate",
        pattern=r"^(cpu|memory|temperature|latency|error_rate)$",
    )
    intensity: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Anomaly intensity multiplier",
    )
    duration_seconds: float = Field(
        default=3.0,
        ge=0.1,
        le=60.0,
        description="Duration in seconds",
    )
