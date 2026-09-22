"""Service-level objective management and evaluation API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_authenticated
from app.db.session import get_db
from app.models.db import Host, SLO, User
from app.models.observability import SLOCreate, SLOResponse, SLOStatusResponse, SLOUpdate
from app.services.audit import add_audit_log
from app.services.slo_service import slo_service

router = APIRouter(prefix="/api/slos", tags=["slos"])


def _validate_host(db: Session, host_id: str) -> Host:
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    if not host.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host is inactive")
    return host


@router.get("", response_model=list[SLOResponse])
def list_slos(
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[SLOResponse]:
    return list(db.scalars(select(SLO).order_by(SLO.name)))


@router.get("/{slo_id}/status", response_model=SLOStatusResponse)
def get_slo_status(
    slo_id: str,
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> SLOStatusResponse:
    slo = db.get(SLO, slo_id)
    if slo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLO not found")
    return SLOStatusResponse(**slo_service.evaluate(db, slo))


@router.post("", response_model=SLOResponse, status_code=status.HTTP_201_CREATED)
def create_slo(
    payload: SLOCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SLOResponse:
    if db.scalar(select(SLO).where(SLO.name == payload.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An SLO with this name already exists")
    _validate_host(db, payload.host_id)

    slo = SLO(
        name=payload.name,
        host_id=payload.host_id,
        metric=payload.metric,
        operator=payload.operator,
        threshold=payload.threshold,
        objective_percent=payload.objective_percent,
        window_hours=payload.window_hours,
        enabled=payload.enabled,
        created_by=current_user.id,
    )
    db.add(slo)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="slo.created",
        resource_type="slo",
        resource_id=slo.id,
        details={"name": slo.name, "host_id": slo.host_id},
    )
    db.commit()
    db.refresh(slo)
    return slo


@router.patch("/{slo_id}", response_model=SLOResponse)
def update_slo(
    slo_id: str,
    payload: SLOUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SLOResponse:
    slo = db.get(SLO, slo_id)
    if slo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLO not found")

    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = " ".join(values["name"].strip().split())
        if not values["name"]:
            raise HTTPException(status_code=422, detail="SLO name must not be blank")
        duplicate = db.scalar(select(SLO).where(SLO.name == values["name"], SLO.id != slo.id))
        if duplicate:
            raise HTTPException(status_code=409, detail="An SLO with this name already exists")
    if "host_id" in values:
        _validate_host(db, values["host_id"])

    for key, value in values.items():
        setattr(slo, key, value)

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="slo.updated",
        resource_type="slo",
        resource_id=slo.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(slo)
    return slo


@router.delete("/{slo_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_slo(
    slo_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    slo = db.get(SLO, slo_id)
    if slo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLO not found")

    db.delete(slo)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="slo.deleted",
        resource_type="slo",
        resource_id=slo.id,
    )
    db.commit()
