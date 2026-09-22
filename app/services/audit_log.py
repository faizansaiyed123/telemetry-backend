"""Centralized audit-log persistence for security and administrative events."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.db import User
from app.models.observability import AuditLogRecord


def record_audit(
    db: Session,
    request: Request,
    *,
    action: str,
    actor: User | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AuditLogRecord:
    """Persist one auditable user/security action."""
    client = request.client
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    ip_address = forwarded_for or (client.host if client else None)
    user_agent = request.headers.get("user-agent")

    entry = AuditLogRecord(
        actor_user_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=dict(metadata) if metadata else None,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    return entry
