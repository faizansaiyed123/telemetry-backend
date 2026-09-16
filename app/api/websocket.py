"""WebSocket endpoint for telemetry streaming."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.telemetry_manager import TelemetryManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket) -> None:
    """WebSocket endpoint that streams telemetry events to connected clients.

    Messages are JSON objects with a 'type' field:
    - 'telemetry': real-time telemetry event data
    - 'alert': anomaly alert (created or resolved)
    - 'system': system status messages (pause, resume, reset, rate change, anomaly trigger)
    """
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
