"""Simulation control REST API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.models.alerts import TriggerAnomalyRequest
from app.models.system import SimulationStatus

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/status", response_model=SimulationStatus)
async def get_simulation_status(request: Request) -> SimulationStatus:
    """Get the current simulation state."""
    manager = request.app.state.telemetry_manager
    return SimulationStatus(
        running=manager.running,
        rate=manager.rate,
        sequence=manager.sequence,
        events_generated=manager.events_generated,
        active_anomaly=manager.active_anomaly,
        connected_clients=manager.connected_clients,
        uptime_seconds=manager.uptime_seconds,
    )


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_simulation(request: Request) -> dict:
    """Start telemetry generation."""
    manager = request.app.state.telemetry_manager
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.start()
    return {"status": "started", "message": "Telemetry generation started"}


@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause_simulation(request: Request) -> dict:
    """Pause telemetry generation."""
    manager = request.app.state.telemetry_manager
    if not manager.running:
        return {"status": "already_paused", "message": "Telemetry generation is already paused"}
    await manager.pause()
    return {"status": "paused", "message": "Telemetry generation paused"}


@router.post("/resume", status_code=status.HTTP_200_OK)
async def resume_simulation(request: Request) -> dict:
    """Resume telemetry generation."""
    manager = request.app.state.telemetry_manager
    if manager.running:
        return {"status": "already_running", "message": "Telemetry generation is already running"}
    await manager.resume()
    return {"status": "resumed", "message": "Telemetry generation resumed"}


@router.post("/reset", status_code=status.HTTP_200_OK)
async def reset_simulation(request: Request) -> dict:
    """Reset all telemetry state."""
    manager = request.app.state.telemetry_manager
    await manager.reset()
    return {"status": "reset", "message": "Telemetry state has been reset"}


@router.post("/rate", status_code=status.HTTP_200_OK)
async def set_rate(request: Request, rate: int = Query(..., ge=1, description="New telemetry rate (events/sec)")) -> dict:
    """Set the telemetry rate."""
    manager = request.app.state.telemetry_manager
    try:
        await manager.set_rate(rate)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"status": "rate_set", "rate": rate, "message": f"Telemetry rate set to {rate}/sec"}


@router.post("/trigger", status_code=status.HTTP_200_OK)
async def trigger_anomaly(request: Request, body: TriggerAnomalyRequest) -> dict:
    """Trigger an anomaly on a specific metric."""
    manager = request.app.state.telemetry_manager
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
