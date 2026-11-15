"""WebSocket endpoint for per-identity telemetry streaming."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_websocket_token
from app.core.ws_tokens import ws_token_registry
from app.db.session import SessionLocal
from app.models.db import User
from app.services.telemetry_manager import TelemetryManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


def _is_expired(user: User) -> bool:
    expires_at = user.demo_expires_at
    if expires_at is None:
        return user.is_demo
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _authenticate_websocket(token: str | None) -> User | None:
    """Validate and consume a short-lived, one-time WebSocket token."""
    if not token:
        return None
    try:
        payload = decode_websocket_token(token)
        subject = payload.get("sub")
        jti = payload.get("jti")
        if not subject or not isinstance(jti, str):
            return None
        if not ws_token_registry.consume(jti):
            return None

        with SessionLocal() as db:
            user = db.scalar(
                select(User).where(User.id == subject, User.is_active.is_(True))
            )
            if user is None:
                return None
            if _is_expired(user):
                return None
            db.expunge(user)
            return user
    except Exception:
        logger.debug("WebSocket authentication failed", exc_info=True)
        return None


@router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket) -> None:
    """Stream only the telemetry runtime owned by the authenticated identity."""
    user = _authenticate_websocket(websocket.query_params.get("token"))
    if user is None:
        await websocket.close(code=1008, reason="Authentication required")
        return

    if user.is_demo:
        registry = getattr(websocket.app.state, "demo_telemetry_registry", None)
        manager = await registry.get(user.id) if registry is not None else None
        if manager is None:
            await websocket.close(code=1008, reason="Demo session is no longer active")
            return
    else:
        manager = websocket.app.state.telemetry_manager

    manager: TelemetryManager
    await websocket.accept()
    await manager.ws_manager.register(websocket)

    try:
        while True:
            try:
                data = await websocket.receive_text()
                _ = json.loads(data) if data else {}
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                logger.debug("Received malformed WebSocket message from client")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket client disconnected unexpectedly")
    finally:
        await manager.ws_manager.unregister(websocket)
