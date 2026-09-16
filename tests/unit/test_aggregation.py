"""Tests for the aggregation service."""

import pytest

from app.models.telemetry import TelemetryEvent
from app.services.aggregation import compute_stats
from app.utils.time import utc_now


def make_event(
    sequence: int = 0,
    cpu: float = 50.0,
    memory: float = 60.0,
    temperature: float = 50.0,
    network_mbps: float = 100.0,
    requests_per_second: int = 500,
    error_rate: float = 0.5,
    latency_ms: float = 30.0,
) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=utc_now(),
        sequence=sequence,
        cpu=cpu,
        memory=memory,
        temperature=temperature,
        network_mbps=network_mbps,
        requests_per_second=requests_per_second,
        error_rate=error_rate,
        latency_ms=latency_ms,
    )


class TestAggregation:
    def test_empty_dataset(self):
        stats = compute_stats([])
        assert stats.count == 0
        assert stats.cpu["min"] is None
        assert stats.cpu["max"] is None
        assert stats.cpu["avg"] is None
        assert stats.cpu["latest"] is None

    def test_single_event(self):
        event = make_event(cpu=50.0)
        stats = compute_stats([event])
        assert stats.count == 1
        assert stats.cpu["min"] == 50.0
        assert stats.cpu["max"] == 50.0
        assert stats.cpu["avg"] == 50.0
        assert stats.cpu["latest"] == 50.0

    def test_min_max_avg(self):
        events = [
            make_event(sequence=0, cpu=10.0),
            make_event(sequence=1, cpu=50.0),
            make_event(sequence=2, cpu=90.0),
        ]
        stats = compute_stats(events)
        assert stats.count == 3
        assert stats.cpu["min"] == 10.0
        assert stats.cpu["max"] == 90.0
        assert stats.cpu["avg"] == pytest.approx(50.0)
        assert stats.cpu["latest"] == 90.0

    def test_latest_is_last_event(self):
        events = [
            make_event(sequence=0, cpu=10.0),
            make_event(sequence=1, cpu=20.0),
            make_event(sequence=2, cpu=30.0),
        ]
        stats = compute_stats(events)
        assert stats.cpu["latest"] == 30.0

    def test_percentage_change(self):
        events = [
            make_event(sequence=0, cpu=50.0),
            make_event(sequence=1, cpu=75.0),
        ]
        stats = compute_stats(events)
        assert stats.cpu["pct_change"] == pytest.approx(50.0)

    def test_percentage_change_zero_first(self):
        events = [
            make_event(sequence=0, cpu=0.0),
            make_event(sequence=1, cpu=50.0),
        ]
        stats = compute_stats(events)
        assert stats.cpu["pct_change"] is None

    def test_all_metrics_present(self):
        events = [make_event()]
        stats = compute_stats(events)
        for field in ["cpu", "memory", "temperature", "network_mbps", "requests_per_second", "error_rate", "latency_ms"]:
            assert field in stats.model_dump()
            assert stats.model_dump()[field]["latest"] is not None

    def test_multiple_metrics(self):
        events = [
            make_event(sequence=0, cpu=10, memory=20, temperature=30),
            make_event(sequence=1, cpu=40, memory=50, temperature=60),
            make_event(sequence=2, cpu=70, memory=80, temperature=90),
        ]
        stats = compute_stats(events)
        assert stats.cpu["min"] == 10
        assert stats.cpu["max"] == 70
        assert stats.memory["min"] == 20
        assert stats.memory["max"] == 80
        assert stats.temperature["min"] == 30
        assert stats.temperature["max"] == 90

    def test_deterministic(self):
        events = [make_event(sequence=i, cpu=float(i * 10)) for i in range(10)]
        stats1 = compute_stats(events)
        stats2 = compute_stats(events)
        assert stats1.model_dump() == stats2.model_dump()
