"""Deterministic rolling z-score anomaly detector.

Uses a rolling z-score: z = (value - mean) / std_dev
Does not flag anomalies until sufficient history exists.
Handles zero standard deviation safely.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from app.models.alerts import Severity
from app.models.telemetry import TelemetryEvent


@dataclass
class AnomalyResult:
    """Result of an anomaly check for a single metric."""

    metric: str
    value: float
    baseline: float
    z_score: float
    is_anomaly: bool
    severity: Severity


METRICS_TO_MONITOR = [
    "cpu",
    "memory",
    "temperature",
    "network_mbps",
    "requests_per_second",
    "error_rate",
    "latency_ms",
]

# Minimum number of data points before anomaly detection activates
MIN_HISTORY = 30


class AnomalyDetector:
    """Rolling z-score anomaly detector for telemetry metrics."""

    def __init__(
        self,
        threshold: float = 3.0,
        window_size: int = 100,
        min_history: int = MIN_HISTORY,
    ) -> None:
        self.threshold = threshold
        self.window_size = window_size
        self.min_history = min_history
        self._history: dict[tuple[str, str], deque[float]] = {}

    def update(self, event: TelemetryEvent) -> list[AnomalyResult]:
        """Process a telemetry event and return any anomaly results.

        Args:
            event: A TelemetryEvent instance.

        Returns:
            List of AnomalyResult for all monitored metrics.
        """
        results: list[AnomalyResult] = []

        host_key = str(event.host_id or "system")
        for metric in METRICS_TO_MONITOR:
            value = getattr(event, metric)
            key = (host_key, metric)
            history = self._history.setdefault(key, deque(maxlen=self.window_size))
            history.append(float(value))

            if len(history) < self.min_history:
                results.append(
                    AnomalyResult(
                        metric=metric,
                        value=value,
                        baseline=value,
                        z_score=0.0,
                        is_anomaly=False,
                        severity=Severity.INFO,
                    )
                )
                continue

            mean = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / len(history)
            std_dev = math.sqrt(variance)

            if std_dev == 0:
                results.append(
                    AnomalyResult(
                        metric=metric,
                        value=value,
                        baseline=round(mean, 2),
                        z_score=0.0,
                        is_anomaly=False,
                        severity=Severity.INFO,
                    )
                )
                continue

            z_score = (value - mean) / std_dev
            is_anomaly = abs(z_score) > self.threshold

            if is_anomaly:
                if abs(z_score) > self.threshold * 2:
                    severity = Severity.CRITICAL
                else:
                    severity = Severity.WARNING
            else:
                severity = Severity.INFO

            results.append(
                AnomalyResult(
                    metric=metric,
                    value=value,
                    baseline=round(mean, 2),
                    z_score=round(z_score, 2),
                    is_anomaly=is_anomaly,
                    severity=severity,
                )
            )

        return results

    def reset(self) -> None:
        """Clear all history."""
        self._history.clear()
