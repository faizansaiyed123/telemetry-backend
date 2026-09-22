"""Notification channel and delivery administration API."""

from __future__ import annotations

from urllib.parse import urlsplit

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
)
from app.services.audit import add_audit_log
from app.services.notification_dispatcher import validate_webhook_url

router = APIRouter(prefix="/api/notification-channels", tags=["notifications"])


def _mask_webhook_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or "configured-destination"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}/…"


def _to_response(channel: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=channel.id,
        name=channel.name,
        channel_type=channel.channel_type,
        webhook_url_masked=_mask_webhook_url(channel.webhook_url),
        min_severity=channel.min_severity,
        notify_alerts=channel.notify_alerts,
        notify_incidents=channel.notify_incidents,
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
    return [_to_response(channel) for channel in db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))]


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
        validate_webhook_url(payload.webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    channel = NotificationChannel(
        name=payload.name,
        webhook_url=payload.webhook_url.strip(),
        min_severity=payload.min_severity,
        notify_alerts=payload.notify_alerts,
        notify_incidents=payload.notify_incidents,
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
        details={"name": channel.name, "min_severity": channel.min_severity},
    )
    db.commit()
    db.refresh(channel)
    return _to_response(channel)





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
        duplicate = db.scalar(
            select(NotificationChannel).where(
                NotificationChannel.name == values["name"],
                NotificationChannel.id != channel.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A notification channel with this name already exists")

    if "webhook_url" in values:
        try:
            validate_webhook_url(values["webhook_url"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values["webhook_url"] = values["webhook_url"].strip()

    for field, value in values.items():
        setattr(channel, field, value)

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
    return _to_response(channel)


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


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
def list_deliveries(
    channel_id: str | None = Query(default=None),
    delivery_status: str | None = Query(default=None, alias="status", pattern=r"^(pending|delivered|failed)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[NotificationDeliveryResponse]:
    stmt = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(limit)
    if channel_id is not None:
        stmt = stmt.where(NotificationDelivery.channel_id == channel_id)
    if delivery_status is not None:
        stmt = stmt.where(NotificationDelivery.status == delivery_status)
    return list(db.scalars(stmt))"""Notification channel and delivery administration API."""
from __future__ import annotations

from urllib.parse import urlsplit

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
)
from app.services.audit import add_audit_log
from app.services.notification_dispatcher import validate_webhook_url

router = APIRouter(prefix="/api/notification-channels", tags=["notifications"])


def _mask_webhook_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or "configured-destination"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}/…"


def _to_response(channel: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=channel.id,
        name=channel.name,
        channel_type=channel.channel_type,
        webhook_url_masked=_mask_webhook_url(channel.webhook_url),
        min_severity=channel.min_severity,
        notify_alerts=channel.notify_alerts,
        notify_incidents=channel.notify_incidents,
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
    return [_to_response(channel) for channel in db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))]


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
        validate_webhook_url(payload.webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    channel = NotificationChannel(
        name=payload.name,
        webhook_url=payload.webhook_url.strip(),
        min_severity=payload.min_severity,
        notify_alerts=payload.notify_alerts,
        notify_incidents=payload.notify_incidents,
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
        details={"name": channel.name, "min_severity": channel.min_severity},
    )
    db.commit()
    db.refresh(channel)
    return _to_response(channel)


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
        duplicate = db.scalar(
            select(NotificationChannel).where(
                NotificationChannel.name == values["name"],
                NotificationChannel.id != channel.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A notification channel with this name already exists")

    if "webhook_url" in values:
        try:
            validate_webhook_url(values["webhook_url"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values["webhook_url"] = values["webhook_url"].strip()

    for field, value in values.items():
        setattr(channel, field, value)

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
    return _to_response(channel)


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


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
def list_deliveries(
    channel_id: str | None = Query(default=None),
    delivery_status: str | None = Query(default=None, alias="status", pattern=r"^(pending|delivered|failed)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[NotificationDeliveryResponse]:
    stmt = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(limit)
    if channel_id is not None:
        stmt = stmt.where(NotificationDelivery.channel_id == channel_id)
    if delivery_status is not None:
        stmt = stmt.where(NotificationDelivery.status == delivery_status)
    return list(db.scalars(stmt))
