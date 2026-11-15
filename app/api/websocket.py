"""WebSocket endpoint for telemetry streaming."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_websocket_token
from app.core.ws_tokens import ws_token_registry
from app.db.session import SessionLocal
from app.models.db import User
from app.services.telemetry_manager import TelemetryManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


def _authenticate_websocket(token: str | None) -> bool:
    """Validate and consume a short-lived, one-time WebSocket token."""
    if not token:
        return False
    try:
        payload = decode_websocket_token(token)
        subject = payload.get("sub")
        jti = payload.get("jti")
        if not subject or not isinstance(jti, str):
            return False
        if not ws_token_registry.consume(jti):
            return False

        with SessionLocal() as db:
            return db.scalar(
                select(User.id).where(User.id == subject, User.is_active.is_(True))
            ) is not None
    except Exception:
        logger.debug("WebSocket authentication failed", exc_info=True)
        return False


@router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket) -> None:
    """Stream telemetry events to authenticated clients."""
    if not _authenticate_websocket(websocket.query_params.get("token")):
        await websocket.close(code=1008, reason="Authentication required")
        return

    manager: TelemetryManager = websocket.app.state.telemetry_manager
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
