"""Low-dependency internal metrics for observing the observability service."""

from __future__ import annotations

from threading import Lock
from time import monotonic


_COUNTER_NAMES = (
    "telemetry_generated_total",
    "telemetry_ingested_total",
    "telemetry_ingestion_batches_total",
    "telemetry_ingestion_rejected_total",
    "telemetry_ingestion_deduplicated_total",
    "telemetry_persisted_total",
    "telemetry_persistence_dropped_total",
    "alert_persistence_dropped_total",
    "alert_created_total",
    "alert_resolved_total",
    "incident_created_total",
    "incident_resolved_total",
    "incident_persistence_dropped_total",
    "audit_events_total",
    "auth_rate_limited_total",
    "ingestion_rate_limited_total",
    "websocket_connections_total",
    "http_requests_total",
    "http_4xx_total",
    "http_5xx_total",
    "http_request_duration_seconds_sum",
    "http_request_duration_seconds_count",
    "telemetry_retention_runs_total",
    "telemetry_retention_deleted_total",
    "telemetry_retention_errors_total",
    "event_bus_published_total",
    "event_bus_received_total",
    "event_bus_dropped_total",
    "event_bus_connection_errors_total",
    "event_bus_publish_errors_total",
    "event_bus_listener_errors_total",
    "event_bus_invalid_messages_total",
    "event_bus_callback_errors_total",
)

_GAUGE_NAMES = (
    "websocket_clients",
    "telemetry_persistence_queue_depth",
    "alert_persistence_queue_depth",
    "incident_persistence_queue_depth",
    "open_incidents",
    "event_bus_queue_depth",
    "event_bus_publisher_connected",
    "event_bus_listener_connected",
)


class PlatformMetrics:
    """Thread-safe process-local counters and gauges.

    These metrics intentionally avoid external dependencies so the service can
    expose useful operational telemetry even in a zero-cost local deployment.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._started = monotonic()
        self._counters = {name: 0 for name in _COUNTER_NAMES}
        self._gauges = {name: 0 for name in _GAUGE_NAMES}

    def increment(self, name: str, amount: int | float = 1) -> int | float:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount
            return self._counters[name]

    def set_gauge(self, name: str, value: int | float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "uptime_seconds": round(monotonic() - self._started, 3),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }

    def prometheus_text(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP telemetry_platform_uptime_seconds Process uptime in seconds.",
            "# TYPE telemetry_platform_uptime_seconds gauge",
            f"telemetry_platform_uptime_seconds {snapshot['uptime_seconds']}",
        ]

        counters = snapshot["counters"]
        gauges = snapshot["gauges"]

        for name, value in counters.items():
            metric = f"telemetry_platform_{name}"
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric} {value}")

        for name, value in gauges.items():
            metric = f"telemetry_platform_{name}"
            lines.append(f"# TYPE {metric} gauge")
            lines.append(f"{metric} {value}")

        return "\n".join(lines) + "\n"


platform_metrics = PlatformMetrics()
