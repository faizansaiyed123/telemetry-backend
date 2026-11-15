"""Simulation control REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import require_authenticated, require_operator_or_demo, resolve_telemetry_manager
from app.models.alerts import TriggerAnomalyRequest
from app.models.db import User
from app.models.system import SimulationStatus

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/status", response_model=SimulationStatus)
async def get_simulation_status(
    request: Request,
    current_user: User = Depends(require_authenticated),
) -> SimulationStatus:
    """Get the simulation state owned by the authenticated identity."""
    manager = await resolve_telemetry_manager(request, current_user)
    return SimulationStatus(
        running=manager.running,
        simulation_enabled=manager.simulation_enabled,
        source_mode=manager.source_mode,
        rate=manager.rate,
        sequence=manager.sequence,
        events_generated=manager.events_generated,
        events_ingested=manager.events_ingested,
        active_anomaly=manager.active_anomaly,
        connected_clients=manager.connected_clients,
        uptime_seconds=manager.uptime_seconds,
    )


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_simulation(
    request: Request,
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Start the caller's telemetry generation."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.start()
    return {"status": "started", "message": "Telemetry generation started"}


@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause_simulation(
    request: Request,
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Pause the caller's telemetry generation."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if not manager.running:
        return {"status": "already_paused", "message": "Telemetry generation is already paused"}
    await manager.pause()
    return {"status": "paused", "message": "Telemetry generation paused"}


@router.post("/resume", status_code=status.HTTP_200_OK)
async def resume_simulation(
    request: Request,
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Resume the caller's telemetry generation."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.resume()
    return {"status": "resumed", "message": "Telemetry generation resumed"}


@router.post("/reset", status_code=status.HTTP_200_OK)
async def reset_simulation(
    request: Request,
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Reset only the caller's telemetry state."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    await manager.reset()
    return {"status": "reset", "message": "Telemetry state has been reset"}


@router.post("/rate", status_code=status.HTTP_200_OK)
async def set_rate(
    request: Request,
    rate: int = Query(..., ge=1, description="New telemetry rate (events/sec)"),
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Set the caller's telemetry rate."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    try:
        await manager.set_rate(rate)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"status": "rate_set", "rate": rate, "message": f"Telemetry rate set to {rate}/sec"}


@router.post("/trigger", status_code=status.HTTP_200_OK)
async def trigger_anomaly(
    request: Request,
    body: TriggerAnomalyRequest,
    current_user: User = Depends(require_operator_or_demo),
) -> dict:
    """Trigger an anomaly only in the caller's simulation."""
    manager = await resolve_telemetry_manager(request, current_user)
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    await manager.trigger_anomaly(
        body.metric,
        intensity=body.intensity,
        duration_seconds=body.duration_seconds,
    )
    return {
        "status": "triggered",
        "metric": body.metric,
        "intensity": body.intensity,
        "duration_seconds": body.duration_seconds,
        "message": f"Anomaly triggered on {body.metric}",
    }
