"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.hosts import router as hosts_router
from app.api.simulation import router as simulation_router
from app.api.telemetry import router as telemetry_router
from app.api.websocket import router as websocket_router
from app.core.bootstrap import bootstrap_admin
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.telemetry_manager import TelemetryManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    bootstrap_admin()
    logger.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
    manager = TelemetryManager(max_history_size=settings.max_history_size, telemetry_rate=settings.telemetry_rate, max_rate=settings.max_telemetry_rate, anomaly_threshold=settings.anomaly_z_threshold)
    app.state.telemetry_manager = manager
    await manager.start()
    yield
    await manager.stop()
    await manager.ws_manager.disconnect_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", description="Real-time telemetry dashboard backend with WebSocket streaming", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(hosts_router)
    app.include_router(telemetry_router)
    app.include_router(alerts_router)
    app.include_router(simulation_router)
    app.include_router(websocket_router)
    return app


app = create_app()
