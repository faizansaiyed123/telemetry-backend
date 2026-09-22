"""Change-event ingestion and query API."""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_authenticated, require_operator
from app.db.session import get_db
from app.models.db import ChangeEvent, Host, User
from app.models.observability import ChangeEventCreate, ChangeEventResponse
from app.services.audit import add_audit_log
from app.utils.time import utc_now

router = APIRouter(prefix="/api/changes", tags=["changes"])


@router.post("", response_model=ChangeEventResponse, status_code=status.HTTP_201_CREATED)
def create_change_event(
    payload: ChangeEventCreate,
    request: Request,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> ChangeEventResponse:
    if payload.host_id is not None:
        host = db.get(Host, payload.host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="Host not found")
        if not host.is_active:
            raise HTTPException(status_code=400, detail="Host is inactive")

    occurred_at = payload.occurred_at or utc_now()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    else:
        occurred_at = occurred_at.astimezone(timezone.utc)

    event = ChangeEvent(
        host_id=payload.host_id,
        event_type=payload.event_type,
        title=payload.title,
        description=payload.description,
        source=payload.source,
        actor_user_id=current_user.id,
        external_ref=payload.external_ref,
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="change_event.created",
        resource_type="change_event",
        resource_id=event.id,
        details={
            "event_type": event.event_type,
            "host_id": event.host_id,
            "source": event.source,
        },
    )
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[ChangeEventResponse])
def list_change_events(
    host_id: str | None = Query(default=None),
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[ChangeEventResponse]:
    stmt = select(ChangeEvent).order_by(ChangeEvent.occurred_at.desc()).limit(200)
    if host_id is not None:
        stmt = stmt.where(ChangeEvent.host_id == host_id)
    return list(db.scalars(stmt))
