"""Aggregation service for telemetry statistics.

Computes min, max, avg, count, latest, and percentage changes
over a set of telemetry events. Deterministic and testable.
"""

from __future__ import annotations

from app.models.telemetry import TelemetryEvent, TelemetryStats


METRIC_FIELDS = [
    "cpu",
    "memory",
    "temperature",
    "network_mbps",
    "requests_per_second",
    "error_rate",
    "latency_ms",
]


def compute_stats(events: list[TelemetryEvent]) -> TelemetryStats:
    """Compute aggregated statistics over a list of telemetry events.

    Handles empty datasets and single-event datasets.
    """
    count = len(events)

    stats: dict[str, dict[str, float | None]] = {}
    for field in METRIC_FIELDS:
        if count == 0:
            stats[field] = {"min": None, "max": None, "avg": None, "latest": None}
            continue

        values = [getattr(e, field) for e in events]
        latest = values[-1]
        first = values[0]

        avg = sum(values) / count
        pct_change = None
        if first != 0:
            pct_change = ((latest - first) / first) * 100

        stats[field] = {
            "min": min(values),
            "max": max(values),
            "avg": round(avg, 2),
            "latest": latest,
            "pct_change": round(pct_change, 2) if pct_change is not None else None,
        }

    return TelemetryStats(
        count=count,
        cpu=stats["cpu"],
        memory=stats["memory"],
        temperature=stats["temperature"],
        network_mbps=stats["network_mbps"],
        requests_per_second=stats["requests_per_second"],
        error_rate=stats["error_rate"],
        latency_ms=stats["latency_ms"],
    )
