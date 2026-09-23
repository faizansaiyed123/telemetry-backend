"""Webhook notification channel administration and delivery history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import NotificationChannel, NotificationDelivery, User
from app.models.observability import (
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationChannelUpdate,
    NotificationDeliveryResponse,
    NotificationTestResponse,
)
from app.services.audit import add_audit_log
from app.services.notification_dispatcher import notification_dispatcher

router = APIRouter(prefix="/api/notification-channels", tags=["notifications"])


def _response(channel: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=channel.id,
        name=channel.name,
        url=channel.url,
        event_types=[
            item for item in channel.event_types.split(",") if item
        ],
        min_severity=channel.min_severity,
        enabled=channel.enabled,
        created_by=channel.created_by,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


@router.get("", response_model=list[NotificationChannelResponse])
def list_channels(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[NotificationChannelResponse]:
    channels = list(
        db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))
    )
    return [_response(channel) for channel in channels]


@router.post("", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: NotificationChannelCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NotificationChannelResponse:
    if db.scalar(select(NotificationChannel).where(NotificationChannel.name == payload.name)):
        raise HTTPException(status_code=409, detail="A notification channel with this name already exists")

    try:
        notification_dispatcher.validate_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    channel = NotificationChannel(
        name=payload.name,
        url=payload.url,
        event_types=",".join(payload.event_types),
        min_severity=payload.min_severity,
        enabled=payload.enabled,
        created_by=current_user.id,
    )
    db.add(channel)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="notification_channel.created",
        resource_type="notification_channel",
        resource_id=channel.id,
        details={"name": channel.name, "events": payload.event_types},
    )
    db.commit()
    db.refresh(channel)
    notification_dispatcher.reload()
    return _response(channel)


@router.patch("/{channel_id}", response_model=NotificationChannelResponse)
def update_channel(
    channel_id: str,
    payload: NotificationChannelUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NotificationChannelResponse:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = " ".join(values["name"].strip().split())
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Notification channel name must not be blank")
        duplicate = db.scalar(
            select(NotificationChannel).where(
                NotificationChannel.name == values["name"],
                NotificationChannel.id != channel.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A notification channel with this name already exists")

    if "url" in values:
        try:
            notification_dispatcher.validate_url(values["url"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "event_types" in values:
        values["event_types"] = ",".join(values["event_types"])

    for key, value in values.items():
        setattr(channel, key, value)

    channel.updated_at = datetime.now(timezone.utc)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="notification_channel.updated",
        resource_type="notification_channel",
        resource_id=channel.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(channel)
    notification_dispatcher.reload()
    return _response(channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_channel(
    channel_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    db.delete(channel)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="notification_channel.deleted",
        resource_type="notification_channel",
        resource_id=channel_id,
    )
    db.commit()
    notification_dispatcher.reload()


@router.post("/{channel_id}/test", response_model=NotificationTestResponse, status_code=status.HTTP_202_ACCEPTED)
def test_channel(
    channel_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NotificationTestResponse:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    if not channel.enabled:
        raise HTTPException(status_code=400, detail="Notification channel is disabled")

    try:
        delivery_id = notification_dispatcher.enqueue_test(channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="Notification channel is not available to the dispatcher") from exc

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="notification_channel.tested",
        resource_type="notification_channel",
        resource_id=channel_id,
        details={"delivery_id": delivery_id},
    )
    db.commit()
    return NotificationTestResponse(delivery_id=delivery_id, status="queued")


@router.post("/deliveries/{delivery_id}/retry", response_model=NotificationTestResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_delivery(
    delivery_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NotificationTestResponse:
    if not notification_dispatcher.retry_delivery(delivery_id):
        raise HTTPException(
            status_code=409,
            detail="Delivery is not failed, does not exist, or its channel is unavailable",
        )

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="notification_delivery.retried",
        resource_type="notification_delivery",
        resource_id=delivery_id,
    )
    db.commit()
    return NotificationTestResponse(delivery_id=delivery_id, status="queued")


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
def list_deliveries(
    channel_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(pending|delivering|delivered|failed)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[NotificationDeliveryResponse]:
    stmt = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(limit)
    if channel_id is not None:
        stmt = stmt.where(NotificationDelivery.channel_id == channel_id)
    if status_filter is not None:
        stmt = stmt.where(NotificationDelivery.status == status_filter)
    return list(db.scalars(stmt))
