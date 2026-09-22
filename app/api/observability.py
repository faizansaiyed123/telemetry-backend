"""Internal observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.db import AuditLog, User
from app.models.observability import AuditLogResponse
from app.services.platform_metrics import platform_metrics

router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/metrics")
def get_platform_metrics(
    request: Request,
    _: User = Depends(require_admin),
) -> dict:
    manager = request.app.state.telemetry_manager
    snapshot = platform_metrics.snapshot()
    snapshot["runtime"] = manager.runtime_metrics()
    return snapshot


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def get_prometheus_metrics(
    _: User = Depends(require_admin),
) -> PlainTextResponse:
    return PlainTextResponse(platform_metrics.prometheus_text(), media_type="text/plain; version=0.0.4")


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def get_audit_logs(
    limit: int = 100,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLogResponse]:
    limit = max(1, min(limit, 500))
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))
