"""Host management endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.db import Host, User

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

    model_config = {"from_attributes": True}


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return current_user


@router.get("", response_model=list[HostResponse])
def list_hosts(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(Host).order_by(Host.name)))


@router.post("", response_model=HostResponse, status_code=status.HTTP_201_CREATED)
def create_host(payload: HostCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if db.scalar(select(Host).where(Host.name == name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Host already exists")
    host = Host(id=str(uuid4()), name=name, environment=payload.environment.strip())
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


@router.patch("/{host_id}", response_model=HostResponse)
def update_host(host_id: str, payload: HostUpdate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = values["name"].strip()
        duplicate = db.scalar(select(Host).where(Host.name == values["name"], Host.id != host_id))
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Host already exists")
    if "environment" in values:
        values["environment"] = values["environment"].strip()
    for key, value in values.items():
        setattr(host, key, value)
    db.commit()
    db.refresh(host)
    return host


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_host(host_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    db.delete(host)
    db.commit()
