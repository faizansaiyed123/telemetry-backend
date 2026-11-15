"""Host management endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.db import Host, User
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


class HostCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    environment: str = Field(default="production", min_length=1, max_length=32)


class HostUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    environment: str | None = Field(default=None, min_length=1, max_length=32)
    is_active: bool | None = None


class HostResponse(BaseModel):
    id: str
    name: str
    environment: str
    is_active: bool
    last_seen_at: datetime | None = None
    agent_version: str | None = None

    model_config = {"from_attributes": True}


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return current_user


@router.get("", response_model=list[HostResponse])
def list_hosts(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(Host).order_by(Host.name)))


@router.post("", response_model=HostResponse, status_code=status.HTTP_201_CREATED)
def create_host(
    payload: HostCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    environment = payload.environment.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Host name must not be blank",
        )
    if not environment:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Host environment must not be blank",
        )
    if db.scalar(select(Host).where(Host.name == name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Host already exists")
    host = Host(id=str(uuid4()), name=name, environment=environment)
    db.add(host)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="host.created",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "environment": host.environment},
    )
    db.commit()
    db.refresh(host)
    return host


@router.patch("/{host_id}", response_model=HostResponse)
def update_host(
    host_id: str,
    payload: HostUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    values = payload.model_dump(exclude_unset=True)

    settings = get_settings()
    if settings.telemetry_persistence_enabled and host.name == settings.telemetry_host_name and values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The configured telemetry persistence host is managed by the system and cannot be edited",
        )
    if "name" in values:
        values["name"] = values["name"].strip()
        if not values["name"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Host name must not be blank",
            )
        duplicate = db.scalar(select(Host).where(Host.name == values["name"], Host.id != host_id))
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Host already exists")
    if "environment" in values:
        values["environment"] = values["environment"].strip()
        if not values["environment"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Host environment must not be blank",
            )
    for key, value in values.items():
        setattr(host, key, value)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="host.updated",
        resource_type="host",
        resource_id=host.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(host)
    return host


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_host(
    host_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    settings = get_settings()
    if settings.telemetry_persistence_enabled and host.name == settings.telemetry_host_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The configured telemetry persistence host cannot be deleted",
        )
    db.delete(host)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="host.deleted",
        resource_type="host",
        resource_id=host_id,
    )
    db.commit()
