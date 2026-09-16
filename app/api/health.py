"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.system import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    manager = request.app.state.telemetry_manager
    return HealthResponse(
        status="healthy",
        uptime_seconds=manager.uptime_seconds,
        stream_active=manager.running,
        connected_clients=manager.connected_clients,
        events_generated=manager.events_generated,
    )
