"""WebSocket connection manager.

Manages client connections with safe asyncio concurrency.
A failed client is removed without affecting other clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket client connections and broadcasting."""

    def __init__(self) -> None:
        self._clients: set["WebSocket"] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: "WebSocket") -> None:
        """Register a new WebSocket client."""
        async with self._lock:
            self._clients.add(websocket)
        logger.info("WebSocket client connected (total: %d)", len(self._clients))

    async def unregister(self, websocket: "WebSocket") -> None:
        """Remove a WebSocket client."""
        async with self._lock:
            self._clients.discard(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(self._clients))

    async def broadcast(self, message: str) -> None:
        """Broadcast a JSON string message to all connected clients.

        Failed clients are removed without affecting others.
        """
        async with self._lock:
            if not self._clients:
                return
            clients = list(self._clients)

        # Send concurrently without holding the lock during sends
        results = await asyncio.gather(
            *[self._safe_send(client, message) for client in clients],
            return_exceptions=True,
        )

        # Remove failed clients
        failed = [
            client for client, result in zip(clients, results, strict=False)
            if isinstance(result, Exception)
        ]
        if failed:
            async with self._lock:
                for client in failed:
                    self._clients.discard(client)
            logger.warning("Removed %d failed WebSocket client(s)", len(failed))

    async def _safe_send(self, websocket: "WebSocket", message: str) -> None:
        """Send a message to a single client, raising on failure."""
        await websocket.send_text(message)

    @property
    def client_count(self) -> int:
        """Return the current number of connected clients."""
        return len(self._clients)

    async def disconnect_all(self) -> None:
        """Disconnect all clients (used during shutdown)."""
        async with self._lock:
            clients = list(self._clients)
            self._clients.clear()

        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
        logger.info("Disconnected all WebSocket clients (%d)", len(clients))
