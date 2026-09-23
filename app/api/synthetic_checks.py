"""Synthetic monitoring management and execution API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_authenticated, require_operator
from app.db.session import get_db
from app.models.db import Service, SyntheticCheck, SyntheticCheckRun, User
from app.models.observability import (
    SyntheticCheckCreate,
    SyntheticCheckResponse,
    SyntheticCheckRunListResponse,
    SyntheticCheckRunResponse,
    SyntheticCheckUpdate,
)
from app.services.audit import add_audit_log
from app.services.synthetic_monitor import SyntheticTargetError, validate_synthetic_url

router = APIRouter(prefix="/api/synthetic-checks", tags=["synthetic-monitoring"])


def _validate_service(db: Session, service_id: str | None) -> None:
    if service_id is None:
        return
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")


def _response(
    check: SyntheticCheck,
    latest: SyntheticCheckRun | None = None,
) -> SyntheticCheckResponse:
    return SyntheticCheckResponse(
        id=check.id,
        service_id=check.service_id,
        name=check.name,
        url=check.url,
        method=check.method,
        interval_seconds=check.interval_seconds,
        timeout_seconds=check.timeout_seconds,
        expected_status=check.expected_status,
        enabled=check.enabled,
        created_by=check.created_by,
        created_at=check.created_at,
        updated_at=check.updated_at,
        last_run=SyntheticCheckRunResponse.model_validate(latest) if latest else None,
    )


@router.get("", response_model=list[SyntheticCheckResponse])
def list_checks(
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[SyntheticCheckResponse]:
    checks = list(db.scalars(select(SyntheticCheck).order_by(SyntheticCheck.name)))
    if not checks:
        return []
    check_ids = [check.id for check in checks]
    rows = list(
        db.scalars(
            select(SyntheticCheckRun)
            .where(SyntheticCheckRun.check_id.in_(check_ids))
            .order_by(SyntheticCheckRun.checked_at.desc())
        )
    )
    latest: dict[str, SyntheticCheckRun] = {}
    for row in rows:
        latest.setdefault(row.check_id, row)
    return [_response(check, latest.get(check.id)) for check in checks]


@router.post("", response_model=SyntheticCheckResponse, status_code=status.HTTP_201_CREATED)
def create_check(
    payload: SyntheticCheckCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SyntheticCheckResponse:
    try:
        url = validate_synthetic_url(payload.url)
    except SyntheticTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _validate_service(db, payload.service_id)
    if db.scalar(select(SyntheticCheck).where(SyntheticCheck.name == payload.name)):
        raise HTTPException(status_code=409, detail="A synthetic check with this name already exists")

    check = SyntheticCheck(
        service_id=payload.service_id,
        name=payload.name,
        url=url,
        method=payload.method,
        interval_seconds=payload.interval_seconds,
        timeout_seconds=payload.timeout_seconds,
        expected_status=payload.expected_status,
        enabled=payload.enabled,
        created_by=current_user.id,
    )
    db.add(check)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="synthetic_check.created",
        resource_type="synthetic_check",
        resource_id=check.id,
        details={"name": check.name, "service_id": check.service_id},
    )
    db.commit()
    db.refresh(check)
    request.app.state.synthetic_monitor.sync()
    return _response(check)


@router.patch("/{check_id}", response_model=SyntheticCheckResponse)
def update_check(
    check_id: str,
    payload: SyntheticCheckUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SyntheticCheckResponse:
    check = db.get(SyntheticCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Synthetic check not found")

    values = payload.model_dump(exclude_unset=True)
    if "url" in values:
        try:
            values["url"] = validate_synthetic_url(values["url"])
        except SyntheticTargetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "service_id" in values:
        _validate_service(db, values["service_id"])
    if "name" in values:
        values["name"] = " ".join(values["name"].strip().split())
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Check name must not be blank")
        duplicate = db.scalar(
            select(SyntheticCheck).where(
                SyntheticCheck.name == values["name"],
                SyntheticCheck.id != check.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A synthetic check with this name already exists")

    for key, value in values.items():
        setattr(check, key, value)

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="synthetic_check.updated",
        resource_type="synthetic_check",
        resource_id=check.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(check)
    request.app.state.synthetic_monitor.sync()
    return _response(check)


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_check(
    check_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    check = db.get(SyntheticCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Synthetic check not found")
    db.delete(check)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="synthetic_check.deleted",
        resource_type="synthetic_check",
        resource_id=check_id,
    )
    db.commit()
    request.app.state.synthetic_monitor.sync()


@router.post("/{check_id}/run", response_model=SyntheticCheckRunResponse)
async def run_check_now(
    check_id: str,
    request: Request,
    _: User = Depends(require_operator),
) -> SyntheticCheckRunResponse:
    try:
        run = await request.app.state.synthetic_monitor.run_once(check_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip('"')) from exc
    except SyntheticTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SyntheticCheckRunResponse.model_validate(run)


@router.get("/{check_id}/runs", response_model=SyntheticCheckRunListResponse)
def list_check_runs(
    check_id: str,
    limit: int = 100,
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> SyntheticCheckRunListResponse:
    if db.get(SyntheticCheck, check_id) is None:
        raise HTTPException(status_code=404, detail="Synthetic check not found")
    limit = max(1, min(limit, 500))
    rows = list(
        db.scalars(
            select(SyntheticCheckRun)
            .where(SyntheticCheckRun.check_id == check_id)
            .order_by(SyntheticCheckRun.checked_at.desc())
            .limit(limit)
        )
    )
    return SyntheticCheckRunListResponse(
        check_id=check_id,
        runs=[SyntheticCheckRunResponse.model_validate(row) for row in reversed(rows)],
    )
