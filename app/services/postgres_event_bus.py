"""Optional PostgreSQL-backed event fan-out for multi-worker deployments.

The event bus is deliberately transport-only. A process still owns its local
runtime state and WebSocket connections; PostgreSQL only fans already-created
messages to peer processes. This prevents remote notifications from being
re-processed as telemetry and keeps the single-process path unchanged when the
feature is disabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import psycopg

from app.services.platform_metrics import platform_metrics

logger = logging.getLogger(__name__)

_CHANNEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_MAX_NOTIFY_PAYLOAD_BYTES = 7900


class PostgresEventBus:
    """Best-effort process-to-process fan-out using PostgreSQL LISTEN/NOTIFY.

    The publisher and listener each own a dedicated synchronous Psycopg
    connection in a background thread. This avoids coupling the feature to a
    particular asyncio event-loop implementation, which is especially useful
    for Windows development environments.
    """

    def __init__(
        self,
        database_url: str,
        *,
        channel: str = "telemetry_platform_events",
        max_queue_size: int = 2000,
        on_message: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        if not _CHANNEL_RE.fullmatch(channel):
            raise ValueError("Invalid PostgreSQL notification channel")
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.channel = channel
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_queue_size)
        self._on_message = on_message
        self._origin = uuid4().hex
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._publisher: threading.Thread | None = None
        self._listener: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._publisher is not None or self._listener is not None

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def origin(self) -> str:
        return self._origin

    async def start(self) -> None:
        """Start publisher/listener workers. Repeated starts are harmless."""
        if self.enabled:
            return
        if self._on_message is None:
            raise RuntimeError("An on_message callback is required for event fan-out")

        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._publisher = threading.Thread(
            target=self._publisher_loop,
            name="telemetry-event-publisher",
            daemon=True,
        )
        self._listener = threading.Thread(
            target=self._listener_loop,
            name="telemetry-event-listener",
            daemon=True,
        )
        self._publisher.start()
        self._listener.start()
        platform_metrics.set_gauge("event_bus_queue_depth", 0)
        platform_metrics.set_gauge("event_bus_publisher_connected", 0)
        platform_metrics.set_gauge("event_bus_listener_connected", 0)
        logger.info("PostgreSQL event fan-out enabled on channel=%s", self.channel)

    async def stop(self) -> None:
        """Stop workers without blocking the asyncio event loop."""
        publisher, listener = self._publisher, self._listener
        self._publisher = None
        self._listener = None
        if publisher is None and listener is None:
            return

        self._stop.set()
        await asyncio.to_thread(self._join_threads, publisher, listener)
        self._loop = None
        platform_metrics.set_gauge("event_bus_queue_depth", 0)
        platform_metrics.set_gauge("event_bus_publisher_connected", 0)
        platform_metrics.set_gauge("event_bus_listener_connected", 0)

    def publish(self, message: str) -> bool:
        """Enqueue a local broadcast for peer processes without blocking."""
        if not self.enabled:
            return False

        envelope = json.dumps(
            {"v": 1, "origin": self._origin, "message": message},
            separators=(",", ":"),
        )
        if len(envelope.encode("utf-8")) > _MAX_NOTIFY_PAYLOAD_BYTES:
            platform_metrics.increment("event_bus_dropped_total")
            logger.warning("Event bus payload exceeds PostgreSQL NOTIFY limit; dropping message")
            return False

        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            platform_metrics.increment("event_bus_dropped_total")
            logger.warning("Event bus queue full; dropping distributed fan-out message")
            return False

        platform_metrics.set_gauge("event_bus_queue_depth", self._queue.qsize())
        return True

    def _publisher_loop(self) -> None:
        backoff = 0.5
        connection = None
        try:
            while not self._stop.is_set():
                if connection is None:
                    try:
                        connection = psycopg.connect(self.database_url, autocommit=True)
                        platform_metrics.set_gauge("event_bus_publisher_connected", 1)
                        backoff = 0.5
                    except Exception:
                        platform_metrics.set_gauge("event_bus_publisher_connected", 0)
                        platform_metrics.increment("event_bus_connection_errors_total")
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 8.0)
                        continue

                try:
                    payload = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    connection.execute(
                        "SELECT pg_notify(%s, %s)",
                        (self.channel, payload),
                    )
                    platform_metrics.increment("event_bus_published_total")
                except Exception:
                    platform_metrics.increment("event_bus_publish_errors_total")
                    logger.exception("PostgreSQL event publish failed; message will be retried")
                    try:
                        self._queue.put_nowait(payload)
                    except queue.Full:
                        platform_metrics.increment("event_bus_dropped_total")
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
                finally:
                    platform_metrics.set_gauge("event_bus_queue_depth", self._queue.qsize())
        finally:
            platform_metrics.set_gauge("event_bus_publisher_connected", 0)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _listener_loop(self) -> None:
        backoff = 0.5
        connection = None
        try:
            while not self._stop.is_set():
                if connection is None:
                    try:
                        connection = psycopg.connect(self.database_url, autocommit=True)
                        connection.execute(f"LISTEN {self.channel}")
                        platform_metrics.set_gauge("event_bus_listener_connected", 1)
                        backoff = 0.5
                    except Exception:
                        platform_metrics.set_gauge("event_bus_listener_connected", 0)
                        platform_metrics.increment("event_bus_connection_errors_total")
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 8.0)
                        continue

                try:
                    for notify in connection.notifies(timeout=0.5):
                        if self._stop.is_set():
                            break
                        self._handle_notification(notify.payload)
                except Exception:
                    platform_metrics.increment("event_bus_listener_errors_total")
                    logger.exception("PostgreSQL event listener failed; reconnecting")
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
        finally:
            platform_metrics.set_gauge("event_bus_listener_connected", 0)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _handle_notification(self, payload: str) -> None:
        try:
            envelope = json.loads(payload)
            if envelope.get("v") != 1 or envelope.get("origin") == self._origin:
                return
            message = envelope.get("message")
            if not isinstance(message, str) or not message:
                return
        except (TypeError, ValueError, json.JSONDecodeError):
            platform_metrics.increment("event_bus_invalid_messages_total")
            logger.warning("Ignoring malformed PostgreSQL event payload")
            return

        platform_metrics.increment("event_bus_received_total")
        loop = self._loop
        callback = self._on_message
        if loop is None or callback is None or loop.is_closed():
            return

        future = asyncio.run_coroutine_threadsafe(callback(message), loop)
        future.add_done_callback(self._consume_callback_result)

    @staticmethod
    def _consume_callback_result(future: object) -> None:
        try:
            # concurrent.futures.Future API; kept duck-typed to avoid leaking
            # the implementation detail into the public service contract.
            future.result()  # type: ignore[attr-defined]
        except Exception:
            platform_metrics.increment("event_bus_callback_errors_total")
            logger.exception("Distributed event callback failed")

    @staticmethod
    def _join_threads(
        publisher: threading.Thread | None,
        listener: threading.Thread | None,
    ) -> None:
        deadline = time.monotonic() + 3.0
        for thread in (publisher, listener):
            if thread is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
