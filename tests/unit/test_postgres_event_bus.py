"""Unit tests for PostgreSQL event fan-out behavior."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import PropertyMock, patch

from app.services.postgres_event_bus import PostgresEventBus


async def _noop(_: str) -> None:
    return None


def test_event_bus_normalizes_sqlalchemy_database_url() -> None:
    bus = PostgresEventBus(
        "postgresql+psycopg://telemetry:telemetry@localhost/db",
        on_message=_noop,
    )
    assert bus.database_url == "postgresql://telemetry:telemetry@localhost/db"


def test_event_bus_rejects_invalid_channel_name() -> None:
    try:
        PostgresEventBus(
            "postgresql://telemetry:telemetry@localhost/db",
            channel="events;drop_table",
            on_message=_noop,
        )
    except ValueError as exc:
        assert "channel" in str(exc)
    else:
        raise AssertionError("invalid channel was accepted")


async def test_publish_envelopes_message_when_started() -> None:
    bus = PostgresEventBus(
        "postgresql://telemetry:telemetry@localhost/db",
        on_message=_noop,
        max_queue_size=1,
    )

    with patch.object(type(bus), "enabled", new_callable=PropertyMock, return_value=True):
        assert bus.publish('{"type":"system"}') is True
        payload = bus._queue.get_nowait()

    envelope = json.loads(payload)
    assert envelope["v"] == 1
    assert envelope["origin"] == bus.origin
    assert envelope["message"] == '{"type":"system"}'


async def test_self_origin_notifications_are_ignored() -> None:
    bus = PostgresEventBus(
        "postgresql://telemetry:telemetry@localhost/db",
        on_message=_noop,
    )
    bus._loop = asyncio.get_running_loop()

    with patch("app.services.postgres_event_bus.asyncio.run_coroutine_threadsafe") as schedule:
        payload = json.dumps(
            {
                "v": 1,
                "origin": bus.origin,
                "message": '{"type":"telemetry"}',
            }
        )
        bus._handle_notification(payload)
        schedule.assert_not_called()


async def test_peer_notification_is_forwarded_to_async_callback() -> None:
    received: list[str] = []

    async def callback(message: str) -> None:
        received.append(message)

    bus = PostgresEventBus(
        "postgresql://telemetry:telemetry@localhost/db",
        on_message=callback,
    )
    bus._loop = asyncio.get_running_loop()

    payload = json.dumps(
        {
            "v": 1,
            "origin": "another-process",
            "message": '{"type":"alert","data":{"id":"a1"}}',
        }
    )
    bus._handle_notification(payload)
    await asyncio.sleep(0.05)

    assert received == ['{"type":"alert","data":{"id":"a1"}}']


def test_publish_drops_oversized_payload() -> None:
    bus = PostgresEventBus(
        "postgresql://telemetry:telemetry@localhost/db",
        on_message=_noop,
    )

    with patch.object(type(bus), "enabled", new_callable=PropertyMock, return_value=True):
        assert bus.publish("x" * 10000) is False
