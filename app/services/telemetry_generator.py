"""Telemetry generator — produces realistic correlated telemetry events.

The generator uses baselines, smooth variation, controlled noise, and
correlated metrics to produce realistic-looking telemetry. Triggered
anomalies inject controlled spikes that propagate through correlated metrics.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.models.telemetry import TelemetryEvent
from app.utils.time import utc_now


@dataclass
class AnomalyTrigger:
    """Represents an active anomaly injection."""

    metric: str
    intensity: float = 1.0
    duration: int = 30  # number of events to sustain the anomaly
    elapsed: int = 0

    def is_active(self) -> bool:
        return self.elapsed < self.duration

    def tick(self) -> None:
        self.elapsed += 1


@dataclass
class GeneratorState:
    """Mutable state for the telemetry generator."""

    # Baseline values (smoothed)
    cpu: float = 45.0
    memory: float = 60.0
    temperature: float = 50.0
    network_mbps: float = 100.0
    requests_per_second: int = 500
    error_rate: float = 0.5
    latency_ms: float = 30.0

    # Smooth noise phase offsets for organic variation
    phase: float = field(default_factory=lambda: random.uniform(0, 2 * math.pi))

    # Active anomaly
    anomaly: AnomalyTrigger | None = None


# Metrics that can have anomalies triggered
ANOMALY_METRICS = {"cpu", "memory", "temperature", "latency", "error_rate"}


class TelemetryGenerator:
    """Generates realistic correlated telemetry events."""

    def __init__(self) -> None:
        self.state = GeneratorState()

    def set_anomaly(self, metric: str, intensity: float = 1.0, duration: int = 30) -> None:
        """Trigger an anomaly on the specified metric."""
        if metric not in ANOMALY_METRICS:
            raise ValueError(f"Unknown anomaly metric: {metric}")
        self.state.anomaly = AnomalyTrigger(
            metric=metric,
            intensity=intensity,
            duration=duration,
        )

    def clear_anomaly(self) -> None:
        """Clear the active anomaly."""
        self.state.anomaly = None

    @property
    def active_anomaly(self) -> str | None:
        """Return the name of the active anomaly metric, if any."""
        if self.state.anomaly and self.state.anomaly.is_active():
            return self.state.anomaly.metric
        return None

    def generate(self, sequence: int) -> TelemetryEvent:
        """Generate a single telemetry event with correlated metrics."""
        s = self.state
        s.phase += 0.15

        # Smooth sinusoidal variation around baselines
        cpu_var = 8 * _smooth_noise(s.phase, freq=0.3)
        mem_var = 5 * _smooth_noise(s.phase + 1.0, freq=0.2)
        temp_var = 4 * _smooth_noise(s.phase + 2.0, freq=0.25)
        net_var = 20 * _smooth_noise(s.phase + 3.0, freq=0.4)
        rps_var = 100 * _smooth_noise(s.phase + 4.0, freq=0.35)
        err_var = 0.3 * _smooth_noise(s.phase + 5.0, freq=0.15)
        lat_var = 8 * _smooth_noise(s.phase + 6.0, freq=0.3)

        cpu = _add_noise(s.cpu + cpu_var, 1.5)
        memory = _add_noise(s.memory + mem_var, 1.0)
        temperature = _add_noise(s.temperature + temp_var, 0.8)
        network_mbps = _add_noise(s.network_mbps + net_var, 5.0)
        requests_per_second = int(_add_noise(s.requests_per_second + rps_var, 20))
        error_rate = _add_noise(s.error_rate + err_var, 0.1)
        latency_ms = _add_noise(s.latency_ms + lat_var, 2.0)

        # Apply anomaly injection
        if s.anomaly and s.anomaly.is_active():
            intensity = s.anomaly.intensity * (1.0 - s.anomaly.elapsed / s.anomaly.duration * 0.3)
            metric = s.anomaly.metric
            if metric == "cpu":
                cpu = min(100, cpu + 40 * intensity)
                # Correlated: high CPU → higher latency, temperature
                latency_ms += 20 * intensity
                temperature += 8 * intensity
                error_rate += 1.5 * intensity
            elif metric == "memory":
                memory = min(100, memory + 35 * intensity)
                # Correlated: high memory → more latency
                latency_ms += 15 * intensity
                error_rate += 0.8 * intensity
            elif metric == "temperature":
                temperature = min(120, temperature + 30 * intensity)
                # Correlated: high temp → CPU throttling, errors
                cpu = max(0, cpu - 5 * intensity)
                error_rate += 2.0 * intensity
                latency_ms += 10 * intensity
            elif metric == "latency":
                latency_ms += 60 * intensity
                # Correlated: high latency → more errors
                error_rate += 3.0 * intensity
                requests_per_second = max(0, int(requests_per_second * (1 - 0.2 * intensity)))
            elif metric == "error_rate":
                error_rate = min(100, error_rate + 15 * intensity)
                # Correlated: high error rate → slightly lower effective throughput
                requests_per_second = max(0, int(requests_per_second * (1 - 0.1 * intensity)))

            s.anomaly.tick()
            if not s.anomaly.is_active():
                s.anomaly = None

        # Smooth baselines toward current values (slow drift)
        s.cpu = s.cpu * 0.95 + cpu * 0.05
        s.memory = s.memory * 0.97 + memory * 0.03
        s.temperature = s.temperature * 0.96 + temperature * 0.04
        s.network_mbps = s.network_mbps * 0.95 + network_mbps * 0.05
        s.requests_per_second = int(s.requests_per_second * 0.95 + requests_per_second * 0.05)
        s.error_rate = s.error_rate * 0.97 + error_rate * 0.03
        s.latency_ms = s.latency_ms * 0.95 + latency_ms * 0.05

        # Clamp to valid ranges
        cpu = _clamp(cpu, 0, 100)
        memory = _clamp(memory, 0, 100)
        temperature = _clamp(temperature, 0, 120)
        network_mbps = _clamp(network_mbps, 0, 10000)
        requests_per_second = _clamp(requests_per_second, 0, 1000000)
        error_rate = _clamp(error_rate, 0, 100)
        latency_ms = _clamp(latency_ms, 0, 10000)

        return TelemetryEvent(
            timestamp=utc_now(),
            sequence=sequence,
            cpu=round(cpu, 1),
            memory=round(memory, 1),
            temperature=round(temperature, 1),
            network_mbps=round(network_mbps, 1),
            requests_per_second=requests_per_second,
            error_rate=round(error_rate, 2),
            latency_ms=round(latency_ms, 1),
        )

    def reset(self) -> None:
        """Reset generator state to defaults."""
        self.state = GeneratorState()
        self.clear_anomaly()


def _smooth_noise(phase: float, freq: float = 1.0) -> float:
    """Produce smooth sinusoidal variation between -1 and 1."""
    return math.sin(phase * freq)


def _add_noise(base: float, amplitude: float) -> float:
    """Add Gaussian noise to a base value."""
    return base + random.gauss(0, amplitude)


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp a value to the given range."""
    return max(low, min(high, value))
