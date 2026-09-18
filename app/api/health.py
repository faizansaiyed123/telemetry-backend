"""Health and readiness probe endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.models.system import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness endpoint for process health."""
    manager = request.app.state.telemetry_manager
    return HealthResponse(
        status="healthy",
        uptime_seconds=manager.uptime_seconds,
        stream_active=manager.running,
        connected_clients=manager.connected_clients,
        events_generated=manager.events_generated,
    )


@router.get("/ready", response_model=ReadinessResponse)
def ready(request: Request) -> ReadinessResponse:
    """Readiness endpoint requiring a working database and application manager."""
    manager_available = hasattr(request.app.state, "telemetry_manager")
    database_connected = False

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            database_connected = True
    except SQLAlchemyError:
        database_connected = False

    if not database_connected or not manager_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        )

    return ReadinessResponse(
        status="ready",
        database_connected=True,
        telemetry_manager_available=True,
    )
