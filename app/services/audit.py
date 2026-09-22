"""Audit log helpers for security-sensitive and operational actions."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.db import AuditLog
from app.services.platform_metrics import platform_metrics


def add_audit_log(
    db: Session,
    *,
    request: Request | None,
    actor_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit event to the current transaction."""

    client_host = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None
    event = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        ip_address=client_host,
        user_agent=user_agent,
        details=json.dumps(details, sort_keys=True, default=str) if details else None,
    )
    db.add(event)
    platform_metrics.increment("audit_events_total")
    return event


def write_security_audit(
    *,
    request: Request | None,
    actor_user_id: str | None,
    action: str,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist a standalone security event when no caller transaction exists."""

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        add_audit_log(
            db,
            request=request,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="auth",
            outcome=outcome,
            details=details,
        )
        db.commit()
