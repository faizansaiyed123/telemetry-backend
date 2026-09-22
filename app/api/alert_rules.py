"""Administrator alert rule management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import AlertRule, User
from app.models.observability import AlertRuleCreate, AlertRuleResponse, AlertRuleUpdate
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/alert-rules", tags=["alert-rules"])


@router.get("", response_model=list[AlertRuleResponse])
def list_alert_rules(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AlertRuleResponse]:
    return list(db.scalars(select(AlertRule).order_by(AlertRule.name)))


@router.post("", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    payload: AlertRuleCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AlertRuleResponse:
    if db.scalar(select(AlertRule).where(AlertRule.name == payload.name)):
        raise HTTPException(status_code=409, detail="An alert rule with this name already exists")

    rule = AlertRule(
        name=payload.name,
        metric=payload.metric,
        operator=payload.operator,
        threshold=payload.threshold,
        duration_seconds=payload.duration_seconds,
        cooldown_seconds=payload.cooldown_seconds,
        severity=payload.severity,
        enabled=payload.enabled,
        created_by=current_user.id,
    )
    db.add(rule)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="alert_rule.created",
        resource_type="alert_rule",
        resource_id=rule.id,
        details={"name": rule.name, "metric": rule.metric},
    )
    db.commit()
    db.refresh(rule)
    request.app.state.telemetry_manager.reload_alert_rules(db)
    return rule


@router.patch("/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    payload: AlertRuleUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AlertRuleResponse:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    values = payload.model_dump(exclude_unset=True)
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Rule name must not be blank")
    if "name" in values and db.scalar(
        select(AlertRule).where(AlertRule.name == values["name"], AlertRule.id != rule.id)
    ):
        raise HTTPException(status_code=409, detail="An alert rule with this name already exists")
    if "name" in values:
        values["name"] = " ".join(values["name"].strip().split())
    for key, value in values.items():
        setattr(rule, key, value)

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="alert_rule.updated",
        resource_type="alert_rule",
        resource_id=rule.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(rule)
    request.app.state.telemetry_manager.reload_alert_rules(db)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_alert_rule(
    rule_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    db.delete(rule)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="alert_rule.deleted",
        resource_type="alert_rule",
        resource_id=rule_id,
    )
    db.commit()
    request.app.state.telemetry_manager.reload_alert_rules(db)
