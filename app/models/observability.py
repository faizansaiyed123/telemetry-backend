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
    received: int = Field(ge=1)
    accepted: int = Field(ge=0)
    deduplicated: int = Field(ge=0)
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


SLO_METRICS = ALERT_RULE_METRICS
SLO_OPERATORS = ALERT_RULE_OPERATORS


class SLOCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    host_id: str
    metric: str = Field(pattern=r"^(cpu|memory|temperature|network_mbps|requests_per_second|error_rate|latency_ms)$")
    operator: str = Field(pattern=r"^(>|>=|<|<=)$")
    threshold: float
    objective_percent: float = Field(gt=0, le=100)
    window_hours: int = Field(default=168, ge=1, le=24 * 30)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("SLO name must not be blank")
        return normalized


class SLOUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    host_id: str | None = None
    metric: str | None = Field(default=None, pattern=r"^(cpu|memory|temperature|network_mbps|requests_per_second|error_rate|latency_ms)$")
    operator: str | None = Field(default=None, pattern=r"^(>|>=|<|<=)$")
    threshold: float | None = None
    objective_percent: float | None = Field(default=None, gt=0, le=100)
    window_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    enabled: bool | None = None


class SLOResponse(BaseModel):
    id: str
    name: str
    host_id: str
    metric: str
    operator: str
    threshold: float
    objective_percent: float
    window_hours: int
    enabled: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SLOStatusResponse(BaseModel):
    slo_id: str
    name: str
    host_id: str
    metric: str
    operator: str
    threshold: float
    objective_percent: float
    window_hours: int
    window_start: datetime
    window_end: datetime
    total_samples: int
    good_samples: int
    bad_samples: int
    sli_percent: float
    error_budget_percent: float
    error_budget_remaining_percent: float
    compliant: bool


class ChangeEventCreate(BaseModel):
    event_type: str = Field(default="deployment", pattern=r"^(deployment|config|feature_flag|maintenance|rollback|other)$")
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    host_id: str | None = None
    source: str = Field(default="manual", min_length=2, max_length=32)
    external_ref: str | None = Field(default=None, max_length=256)
    occurred_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Change title must not be blank")
        return normalized


class ChangeEventResponse(BaseModel):
    id: str
    host_id: str | None
    event_type: str
    title: str
    description: str | None
    source: str
    actor_user_id: str | None
    external_ref: str | None
    occurred_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentTimelineItem(BaseModel):
    kind: str = Field(pattern=r"^(alert|change)$")
    timestamp: datetime
    title: str
    severity: str | None = None
    status: str | None = None
    reference_id: str
    source: str | None = None


class IncidentEvidenceResponse(BaseModel):
    incident: IncidentResponse
    timeline: list[IncidentTimelineItem]
    alert_count: int
    metric_count: int
    change_count: int
    correlation_window_minutes: int
    findings: list[str]
    metric_findings: list[str] = Field(default_factory=list)


NOTIFICATION_EVENT_TYPES = (
    "alert.created",
    "alert.resolved",
    "incident.created",
    "incident.resolved",
)
NOTIFICATION_SEVERITIES = ("INFO", "WARNING", "CRITICAL")


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    event_types: list[str] = Field(default_factory=lambda: list(NOTIFICATION_EVENT_TYPES), min_length=1, max_length=4)
    min_severity: str = Field(default="WARNING", pattern=r"^(INFO|WARNING|CRITICAL)$")
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Notification channel name must not be blank")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Webhook URL must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Webhook URL must not contain credentials or a fragment")
        return value.strip()

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value))
        if not normalized or any(item not in NOTIFICATION_EVENT_TYPES for item in normalized):
            raise ValueError(f"event_types must be selected from {NOTIFICATION_EVENT_TYPES}")
        return normalized


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=4)
    min_severity: str | None = Field(default=None, pattern=r"^(INFO|WARNING|CRITICAL)$")
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Notification channel name must not be blank")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from urllib.parse import urlsplit
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Webhook URL must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Webhook URL must not contain credentials or a fragment")
        return value.strip()

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value))
        if not normalized or any(item not in NOTIFICATION_EVENT_TYPES for item in normalized):
            raise ValueError(f"event_types must be selected from {NOTIFICATION_EVENT_TYPES}")
        return normalized


class NotificationChannelResponse(BaseModel):
    id: str
    name: str
    url: str
    event_types: list[str]
    min_severity: str
    enabled: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryResponse(BaseModel):
    id: str
    channel_id: str
    event_type: str
    event_id: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    payload_sha256: str
    created_at: datetime
    delivered_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class NotificationTestResponse(BaseModel):
    delivery_id: str
    status: str
