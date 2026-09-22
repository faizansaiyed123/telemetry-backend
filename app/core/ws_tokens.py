"""Short-lived, single-use tokens for WebSocket authentication."""

from __future__ import annotations

from threading import Lock
from time import time
from uuid import uuid4


class WebSocketTokenRegistry:
    """Issue and consume one-time WebSocket handoff tokens.

    The registry is process-local on purpose, matching the application's
    single-process real-time ownership model.
    """

    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._issued: dict[str, float] = {}

    def register(self, jti: str, expires_at: float) -> None:
        with self._lock:
            self._prune_locked()
            self._issued[jti] = expires_at

    def consume(self, jti: str) -> bool:
        now = time()
        with self._lock:
            expires_at = self._issued.pop(jti, None)
            if expires_at is None:
                self._prune_locked()
                return False
            if expires_at <= now:
                self._prune_locked()
                return False
            return True

    def new_jti(self) -> str:
        return uuid4().hex

    def _prune_locked(self) -> None:
        now = time()
        expired = [jti for jti, expires_at in self._issued.items() if expires_at <= now]
        for jti in expired:
            self._issued.pop(jti, None)


ws_token_registry = WebSocketTokenRegistry()
