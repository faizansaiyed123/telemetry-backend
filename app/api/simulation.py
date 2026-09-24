"""Simulation control REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import require_authenticated, require_operator
from app.models.alerts import TriggerAnomalyRequest
from app.models.db import User
from app.models.system import SimulationStatus

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/status", response_model=SimulationStatus)
async def get_simulation_status(
    request: Request,
    _: User = Depends(require_authenticated),
) -> SimulationStatus:
    """Get the current simulation state."""
    manager = request.app.state.telemetry_manager
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
    _: User = Depends(require_operator),
) -> dict:
    """Start telemetry generation."""
    manager = request.app.state.telemetry_manager
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.start()
    return {"status": "started", "message": "Telemetry generation started"}


@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause_simulation(
    request: Request,
    _: User = Depends(require_operator),
) -> dict:
    """Pause telemetry generation."""
    manager = request.app.state.telemetry_manager
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if not manager.running:
        return {"status": "already_paused", "message": "Telemetry generation is already paused"}
    await manager.pause()
    return {"status": "paused", "message": "Telemetry generation paused"}


@router.post("/resume", status_code=status.HTTP_200_OK)
async def resume_simulation(
    request: Request,
    _: User = Depends(require_operator),
) -> dict:
    """Resume telemetry generation."""
    manager = request.app.state.telemetry_manager
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.resume()
    return {"status": "resumed", "message": "Telemetry generation resumed"}


@router.post("/reset", status_code=status.HTTP_200_OK)
async def reset_simulation(
    request: Request,
    _: User = Depends(require_operator),
) -> dict:
    """Reset all telemetry state."""
    manager = request.app.state.telemetry_manager
    if not manager.simulation_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulation controls are disabled when TELEMETRY_SOURCE_MODE=agent")
    await manager.reset()
    return {"status": "reset", "message": "Telemetry state has been reset"}


@router.post("/rate", status_code=status.HTTP_200_OK)
async def set_rate(
    request: Request,
    rate: int = Query(..., ge=1, description="New telemetry rate (events/sec)"),
    _: User = Depends(require_operator),
) -> dict:
    """Set the telemetry rate."""
    manager = request.app.state.telemetry_manager
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
    _: User = Depends(require_operator),
) -> dict:
    """Trigger an anomaly on a specific metric."""
    manager = request.app.state.telemetry_manager
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
