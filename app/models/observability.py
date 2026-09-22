"""API models for alert rules, incidents, and telemetry ingestion."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALERT_RULE_METRICS = (
    "cpu",
    "memory",
    "temperature",
    "network_mbps",
    "requests_per_second",
    "error_rate",
    "latency_ms",
)
ALERT_RULE_OPERATORS = (">", ">=", "<", "<=")


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    metric: str = Field(pattern=r"^(cpu|memory|temperature|network_mbps|requests_per_second|error_rate|latency_ms)$")
    operator: str = Field(pattern=r"^(>|>=|<|<=)$")
    threshold: float = Field()
    duration_seconds: float = Field(default=0.0, ge=0, le=86400)
    cooldown_seconds: float = Field(default=300.0, ge=0, le=86400)
    severity: str = Field(default="WARNING", pattern=r"^(INFO|WARNING|CRITICAL)$")
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Rule name must not be blank")
        return normalized


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    metric: str | None = Field(default=None, pattern=r"^(cpu|memory|temperature|network_mbps|requests_per_second|error_rate|latency_ms)$")
    operator: str | None = Field(default=None, pattern=r"^(>|>=|<|<=)$")
    threshold: float | None = None
    duration_seconds: float | None = Field(default=None, ge=0, le=86400)
    cooldown_seconds: float | None = Field(default=None, ge=0, le=86400)
    severity: str | None = Field(default=None, pattern=r"^(INFO|WARNING|CRITICAL)$")
    enabled: bool | None = None


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    metric: str
    operator: str
    threshold: float
    duration_seconds: float
    cooldown_seconds: float
    severity: str
    enabled: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IngestTelemetryEvent(BaseModel):
    timestamp: datetime
    sequence: int = Field(ge=0)
    cpu: float = Field(ge=0, le=100)
    memory: float = Field(ge=0, le=100)
    temperature: float = Field(ge=0, le=120)
    network_mbps: float = Field(ge=0, le=10000)
    requests_per_second: float = Field(ge=0, le=1000000)
    error_rate: float = Field(ge=0, le=100)
    latency_ms: float = Field(ge=0, le=10000)


class IngestTelemetryBatch(BaseModel):
    events: list[IngestTelemetryEvent] = Field(min_length=1, max_length=250)
    agent_version: str | None = Field(default=None, max_length=64)


class IngestResponse(BaseModel):
    host_id: str
    accepted: int
    last_sequence: int
    source: str = "agent"


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("API key name must not be blank")
        return normalized


class ApiKeyResponse(BaseModel):
    id: str
    host_id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreatedResponse(ApiKeyResponse):
    secret: str


class IncidentResponse(BaseModel):
    id: str
    host_id: str | None
    title: str
    status: str
    severity: str
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    alert_ids: list[str]
    active_alert_count: int


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    ip_address: str | None
    user_agent: str | None
    details: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
