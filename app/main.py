"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alerts import router as alerts_router
from app.api.health import router as health_router
from app.api.simulation import router as simulation_router
from app.api.telemetry import router as telemetry_router
from app.api.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.telemetry_manager import TelemetryManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    settings = get_settings()
    setup_logging(settings)

    logger.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
    logger.info(
        "Config: rate=%d, max_rate=%d, max_history=%d, anomaly_threshold=%.1f",
        settings.telemetry_rate,
        settings.max_telemetry_rate,
        settings.max_history_size,
        settings.anomaly_z_threshold,
    )

    # Initialize telemetry manager
    manager = TelemetryManager(
        max_history_size=settings.max_history_size,
        telemetry_rate=settings.telemetry_rate,
        max_rate=settings.max_telemetry_rate,
        anomaly_threshold=settings.anomaly_z_threshold,
    )
    app.state.telemetry_manager = manager

    # Start telemetry generation
    await manager.start()

    logger.info("Application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.app_name)
    await manager.stop()
    await manager.ws_manager.disconnect_all()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Real-time telemetry dashboard backend with WebSocket streaming",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(health_router)
    app.include_router(telemetry_router)
    app.include_router(alerts_router)
    app.include_router(simulation_router)
    app.include_router(websocket_router)

    return app


app = create_app()
