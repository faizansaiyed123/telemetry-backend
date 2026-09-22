"""Unit tests for telemetry persistence queue behavior."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.telemetry import TelemetryEvent
from app.services.telemetry_persistence import TelemetryPersistence


def event(sequence: int = 1) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=datetime.now(timezone.utc),
        sequence=sequence,
        cpu=20,
        memory=30,
        temperature=45,
        network_mbps=10,
        requests_per_second=100,
        error_rate=0.2,
        latency_ms=25,
    )


@pytest.mark.asyncio
async def test_enqueue_and_persist_batch() -> None:
    persistence = TelemetryPersistence("host-1", batch_size=10)
    session = MagicMock()
    session.__enter__.return_value = session
    with patch("app.services.telemetry_persistence.SessionLocal", return_value=session):
        persistence.enqueue(event(1))
        persistence.enqueue(event(2))
        await persistence._persist([("host-1", event(1)), ("host-1", event(2))])

    session.execute.assert_called_once()
    rows = session.execute.call_args.args[1]
    assert len(rows) == 2
    assert rows[0]["host_id"] == "host-1"
    assert rows[1]["sequence"] == 2
    session.commit.assert_called_once()
    assert persistence.persisted_events == 2


def test_full_queue_drops_new_event_without_raising() -> None:
    persistence = TelemetryPersistence("host-1", max_queue_size=1)
    persistence.enqueue(event(1))
    persistence.enqueue(event(2))

    assert persistence._queue.qsize() == 1
    assert persistence.dropped_events == 1
