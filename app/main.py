"""FastAPI application entry point."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.alert_rules import router as alert_rules_router
from app.api.alerts import router as alerts_router
from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.changes import router as changes_router
from app.api.health import router as health_router
from app.api.hosts import router as hosts_router
from app.api.incidents import router as incidents_router
from app.api.ingestion import router as ingestion_router
from app.api.observability import router as observability_router
from app.api.notification_channels import router as notification_channels_router
from app.api.simulation import router as simulation_router
from app.api.slos import router as slos_router
from app.api.telemetry import router as telemetry_router
from app.api.users import router as users_router
from app.api.websocket import router as websocket_router
from app.core.bootstrap import bootstrap_data
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.rate_limit import RateLimitExceeded, rate_limiter
from app.core.request_context import reset_request_id, set_request_id
from app.services.notification_dispatcher import notification_dispatcher
from app.services.platform_metrics import platform_metrics
from app.services.telemetry_manager import TelemetryManager
from app.services.telemetry_retention import TelemetryRetentionService

logger = logging.getLogger(__name__)


def _client_key(request: Request) -> str:
    # Use the direct socket peer by default. Forwarded headers are only safe
    # when populated by a trusted proxy layer, so this app does not trust
    # client-controlled X-Forwarded-For for abuse-limit identity.
    return request.client.host if request.client else "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    bootstrap_data()
    logger.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
    manager = TelemetryManager(
        max_history_size=settings.max_history_size,
        telemetry_rate=settings.telemetry_rate,
        max_rate=settings.max_telemetry_rate,
        anomaly_threshold=settings.anomaly_z_threshold,
    )
    app.state.telemetry_manager = manager
    retention = TelemetryRetentionService()
    app.state.telemetry_retention = retention
    await retention.start()
    await notification_dispatcher.start()
    app.state.notification_dispatcher = notification_dispatcher
    await manager.start()
    yield
    await retention.stop()
    await manager.stop()
    await notification_dispatcher.stop()
    await manager.ws_manager.disconnect_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description="Production-oriented real-time telemetry and observability backend",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context_and_abuse_protection(request: Request, call_next):
        incoming_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming_request_id
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", incoming_request_id)
            else uuid4().hex
        )
        token = set_request_id(request_id)
        request.state.request_id = request_id

        path = request.url.path
        rate_key: str | None = None

        if request.method == "POST" and path == "/api/auth/login":
            rate_key = f"login:{_client_key(request)}"
            try:
                rate_limiter.check(rate_key, limit=10, window_seconds=60)
            except RateLimitExceeded as exc:
                platform_metrics.increment("auth_rate_limited_total")
                reset_request_id(token)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many failed authentication attempts", "retry_after": exc.retry_after},
                    headers={
                        "Retry-After": str(exc.retry_after),
                        "X-Request-ID": request_id,
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                        "Referrer-Policy": "no-referrer",
                    },
                )
        elif request.method == "POST" and path == "/api/auth/signup":
            rate_key = f"signup:{_client_key(request)}"
            try:
                rate_limiter.check(rate_key, limit=5, window_seconds=60)
            except RateLimitExceeded as exc:
                platform_metrics.increment("auth_rate_limited_total")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many signup attempts", "retry_after": exc.retry_after},
                    headers={
                        "Retry-After": str(exc.retry_after),
                        "X-Request-ID": request_id,
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                        "Referrer-Policy": "no-referrer",
                    },
                )
        elif request.method == "POST" and path == "/api/ingest/v1/telemetry":
            credential = request.headers.get("x-telemetry-key") or _client_key(request)
            import hashlib
            credential_digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
            rate_key = f"ingest:{credential_digest}"
            try:
                rate_limiter.check(rate_key, limit=120, window_seconds=60)
            except RateLimitExceeded as exc:
                platform_metrics.increment("ingestion_rate_limited_total")
                reset_request_id(token)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many ingestion requests", "retry_after": exc.retry_after},
                    headers={
                        "Retry-After": str(exc.retry_after),
                        "X-Request-ID": request_id,
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                        "Referrer-Policy": "no-referrer",
                    },
                )

        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration = monotonic() - started
            platform_metrics.increment("http_requests_total")
            platform_metrics.increment("http_5xx_total")
            platform_metrics.increment("http_request_duration_seconds_count")
            platform_metrics.increment("http_request_duration_seconds_sum", duration)
            reset_request_id(token)
            raise

        duration = monotonic() - started
        platform_metrics.increment("http_requests_total")
        if response.status_code >= 500:
            platform_metrics.increment("http_5xx_total")
        elif response.status_code >= 400:
            platform_metrics.increment("http_4xx_total")
        platform_metrics.increment("http_request_duration_seconds_count")
        platform_metrics.increment("http_request_duration_seconds_sum", duration)

        if path == "/api/auth/login" and response.status_code < 400 and rate_key is not None:
            rate_limiter.clear(rate_key)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        if settings.app_env.strip().lower() == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        reset_request_id(token)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(changes_router)
    app.include_router(users_router)
    app.include_router(hosts_router)
    app.include_router(telemetry_router)
    app.include_router(alerts_router)
    app.include_router(simulation_router)
    app.include_router(slos_router)
    app.include_router(websocket_router)
    app.include_router(api_keys_router)
    app.include_router(ingestion_router)
    app.include_router(alert_rules_router)
    app.include_router(incidents_router)
    app.include_router(observability_router)
    app.include_router(notification_channels_router)
    return app


app = create_app()
