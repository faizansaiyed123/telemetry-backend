"""CRUD APIs for operator-defined alert rules."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_authenticated
from app.db.session import get_db
from app.models.db import Host, User
from app.models.observability import AlertRuleRecord
from app.services.audit_log import record_audit
from app.services.rule_engine import SUPPORTED_METRICS, SUPPORTED_OPERATORS
from app.utils.time import utc_now

router = APIRouter(prefix="/api/alert-rules", tags=["alert-rules"])
VALID_SEVERITIES = {"INFO", "WARNING", "CRITICAL"}


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host_id: str | None = None
    metric: str
    operator: str
    threshold: float
    duration_seconds: float = Field(default=0, ge=0, le=3600)
    severity: str = Field(default="WARNING")
    enabled: bool = True
    cooldown_seconds: float = Field(default=300, ge=0, le=86400)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, value: str) -> str:
        if value not in SUPPORTED_METRICS:
            raise ValueError(f"Metric must be one of: {', '.join(sorted(SUPPORTED_METRICS))}")
        return value

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, value: str) -> str:
        if value not in SUPPORTED_OPERATORS:
            raise ValueError(f"Operator must be one of: {', '.join(sorted(SUPPORTED_OPERATORS))}")
        return value

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        value = value.upper()
        if value not in VALID_SEVERITIES:
            raise ValueError(f"Severity must be one of: {', '.join(sorted(VALID_SEVERITIES))}")
        return value


class AlertRuleUpdate(AlertRuleCreate):
    pass


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    host_id: str | None
    metric: str
    operator: str
    threshold: float
    duration_seconds: float
    severity: str
    enabled: bool
    cooldown_seconds: float
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    last_fired_at: datetime | None

    model_config = {"from_attributes": True}


async def _refresh(request: Request) -> None:
    await request.app.state.telemetry_manager.refresh_rules()


def _validate_host(payload_host_id: str | None, db: Session) -> None:
    if payload_host_id is None:
        return
    host = db.get(Host, payload_host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    if not host.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alert rules cannot target inactive hosts",
        )


@router.get("", response_model=list[AlertRuleResponse])
def list_alert_rules(
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[AlertRuleResponse]:
    return list(
        db.scalars(
            select(AlertRuleRecord).order_by(
                AlertRuleRecord.enabled.desc(),
                AlertRuleRecord.created_at.desc(),
            )
        )
    )


@router.post("", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    payload: AlertRuleCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AlertRuleResponse:
    _validate_host(payload.host_id, db)
    record = AlertRuleRecord(
        id=str(uuid4()),
        name=payload.name,
        host_id=payload.host_id,
        metric=payload.metric,
        operator=payload.operator,
        threshold=payload.threshold,
        duration_seconds=payload.duration_seconds,
        severity=payload.severity,
        enabled=payload.enabled,
        cooldown_seconds=payload.cooldown_seconds,
        created_by=current_user.id,
    )
    db.add(record)
    record_audit(
        db,
        request,
        action="alert_rule.created",
        actor=current_user,
        target_type="alert_rule",
        target_id=record.id,
        metadata=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    await _refresh(request)
    return record


@router.patch("/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    payload: AlertRuleUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AlertRuleResponse:
    record = db.get(AlertRuleRecord, rule_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")

    _validate_host(payload.host_id, db)
    before = {
        "name": record.name,
        "host_id": record.host_id,
        "metric": record.metric,
        "operator": record.operator,
        "threshold": record.threshold,
        "duration_seconds": record.duration_seconds,
        "severity": record.severity,
        "enabled": record.enabled,
        "cooldown_seconds": record.cooldown_seconds,
    }
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    record.updated_at = utc_now()
    record_audit(
        db,
        request,
        action="alert_rule.updated",
        actor=current_user,
        target_type="alert_rule",
        target_id=record.id,
        metadata={"before": before, "after": payload.model_dump()},
    )
    db.commit()
    db.refresh(record)
    await _refresh(request)
    return record


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    record = db.get(AlertRuleRecord, rule_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")

    record.enabled = False
    record.updated_at = utc_now()
    record_audit(
        db,
        request,
        action="alert_rule.deleted",
        actor=current_user,
        target_type="alert_rule",
        target_id=record.id,
    )
    db.commit()
    await _refresh(request)
