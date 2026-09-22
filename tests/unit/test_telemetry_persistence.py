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
        await persistence._persist([event(1), event(2)])

    assert session.execute.call_count == 2
    assert session.commit.call_count == 1
    assert persistence.persisted_events == 2


def test_full_queue_drops_new_event_without_raising() -> None:
    persistence = TelemetryPersistence("host-1", max_queue_size=1)
    persistence.enqueue(event(1))
    persistence.enqueue(event(2))

    assert persistence._queue.qsize() == 1
    assert persistence.dropped_events == 1
