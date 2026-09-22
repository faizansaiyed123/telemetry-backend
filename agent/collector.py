"""Cross-platform host telemetry collection for the local agent."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

import psutil


@dataclass(frozen=True, slots=True)
class AgentSample:
    """One locally collected telemetry sample."""

    timestamp: float
    sequence: int
    cpu: float
    memory: float
    temperature: float
    network_mbps: float
    requests_per_second: float
    error_rate: float
    latency_ms: float


class SystemTelemetryCollector:
    """Collect real host metrics without OS-specific shell commands."""

    def __init__(
        self,
        *,
        requests_per_second: float = 0.0,
        error_rate: float = 0.0,
        latency_ms: float = 0.0,
        temperature_fallback: float = 0.0,
    ) -> None:
        self.hostname = socket.gethostname()
        self._requests_per_second = requests_per_second
        self._error_rate = error_rate
        self._latency_ms = latency_ms
        self._temperature_fallback = temperature_fallback
        self._last_network = psutil.net_io_counters()
        self._last_sample_at = time.monotonic()
        # Prime psutil's percentage sampler so the first real sample is valid.
        psutil.cpu_percent(interval=None)

    def sample(self) -> AgentSample:
        now = time.monotonic()
        elapsed = max(now - self._last_sample_at, 0.001)
        network = psutil.net_io_counters()

        sent_delta = max(0, network.bytes_sent - self._last_network.bytes_sent)
        recv_delta = max(0, network.bytes_recv - self._last_network.bytes_recv)
        network_mbps = ((sent_delta + recv_delta) * 8 / elapsed) / 1_000_000

        self._last_network = network
        self._last_sample_at = now

        cpu = max(0.0, min(100.0, psutil.cpu_percent(interval=None)))
        memory = max(0.0, min(100.0, psutil.virtual_memory().percent))

        return AgentSample(
            timestamp=time.time(),
            sequence=time.time_ns() // 1_000_000,
            cpu=round(cpu, 2),
            memory=round(memory, 2),
            temperature=round(self._read_temperature(), 2),
            network_mbps=round(max(0.0, network_mbps), 3),
            requests_per_second=round(max(0.0, self._requests_per_second), 3),
            error_rate=round(max(0.0, min(100.0, self._error_rate)), 3),
            latency_ms=round(max(0.0, self._latency_ms), 3),
        )

    def _read_temperature(self) -> float:
        """Return the first available hardware sensor, else the explicit fallback."""
        try:
            sensors = psutil.sensors_temperatures(fahrenheit=False)
        except (AttributeError, NotImplementedError):
            return self._temperature_fallback

        for entries in sensors.values():
            for entry in entries:
                current = getattr(entry, "current", None)
                if current is not None:
                    return float(current)
        return self._temperature_fallback
