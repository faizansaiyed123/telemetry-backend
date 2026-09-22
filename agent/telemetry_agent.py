"""Zero-cost host telemetry agent for the Telemetry platform.

Collects real local host metrics with psutil and ships bounded batches to the
authenticated ingestion API. An optional HTTP probe adds live request,
latency, and error signals without requiring a paid monitoring service.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

import httpx
import psutil


@dataclass(frozen=True)
class AgentConfig:
    api_url: str
    api_key: str
    interval_seconds: float = 2.0
    batch_size: int = 10
    agent_version: str = "telemetry-agent/1.0"
    probe_url: str | None = None
    verify_tls: bool = True
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "AgentConfig":
        api_url = os.environ.get("TELEMETRY_API_URL", "").rstrip("/")
        api_key = os.environ.get("TELEMETRY_API_KEY", "")
        if not api_url:
            raise ValueError("TELEMETRY_API_URL is required")
        if not api_key:
            raise ValueError("TELEMETRY_API_KEY is required")
        return cls(
            api_url=api_url,
            api_key=api_key,
            interval_seconds=max(0.5, float(os.environ.get("TELEMETRY_AGENT_INTERVAL", "2"))),
            batch_size=max(1, min(250, int(os.environ.get("TELEMETRY_AGENT_BATCH_SIZE", "10")))),
            agent_version=os.environ.get("TELEMETRY_AGENT_VERSION", "telemetry-agent/1.0"),
            probe_url=os.environ.get("TELEMETRY_PROBE_URL") or None,
            verify_tls=os.environ.get("TELEMETRY_VERIFY_TLS", "true").lower() not in {"0", "false", "no"},
            timeout_seconds=max(1.0, float(os.environ.get("TELEMETRY_AGENT_TIMEOUT", "10"))),
        )


class HostCollector:
    """Collect real host metrics and optionally probe an HTTP service."""

    def __init__(self, probe_url: str | None) -> None:
        self.probe_url = probe_url
        self._previous_net = psutil.net_io_counters()
        self._previous_at = time.monotonic()
        self._sequence = 0
        self._stop = Event()

        # Prime psutil's non-blocking CPU sampler.
        psutil.cpu_percent(interval=None)

    def stop(self) -> None:
        self._stop.set()

    def collect(self) -> dict:
        now = time.monotonic()
        net = psutil.net_io_counters()
        elapsed = max(0.001, now - self._previous_at)
        bytes_delta = (net.bytes_sent + net.bytes_recv) - (
            self._previous_net.bytes_sent + self._previous_net.bytes_recv
        )
        network_mbps = max(0.0, (bytes_delta * 8) / elapsed / 1_000_000)
        self._previous_net = net
        self._previous_at = now
        self._sequence += 1

        cpu = float(psutil.cpu_percent(interval=None))
        memory = float(psutil.virtual_memory().percent)
        temperature = self._temperature()

        requests_per_second = 0.0
        error_rate = 0.0
        latency_ms = 0.0
        if self.probe_url:
            requests_per_second, error_rate, latency_ms = self._probe()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self._sequence,
            "cpu": min(100.0, max(0.0, cpu)),
            "memory": min(100.0, max(0.0, memory)),
            "temperature": min(120.0, max(0.0, temperature)),
            "network_mbps": min(10000.0, max(0.0, network_mbps)),
            "requests_per_second": max(0.0, requests_per_second),
            "error_rate": min(100.0, max(0.0, error_rate)),
            "latency_ms": min(10000.0, max(0.0, latency_ms)),
        }

    @staticmethod
    def _temperature() -> float:
        try:
            sensors = psutil.sensors_temperatures(fahrenheit=False)
        except (AttributeError, OSError):
            return 0.0
        for entries in sensors.values():
            for reading in entries:
                if reading.current is not None and reading.current >= 0:
                    return float(reading.current)
        return 0.0

    def _probe(self) -> tuple[float, float, float]:
        started = time.perf_counter()
        try:
            response = httpx.get(self.probe_url, timeout=5.0)
            latency_ms = (time.perf_counter() - started) * 1000
            return 1.0 / max(0.001, latency_ms / 1000), 0.0 if response.is_success else 100.0, latency_ms
        except httpx.HTTPError:
            latency_ms = (time.perf_counter() - started) * 1000
            return 1.0 / max(0.001, latency_ms / 1000), 100.0, latency_ms

    def run(self, config: AgentConfig) -> None:
        headers = {"X-Telemetry-Key": config.api_key}
        endpoint = f"{config.api_url}/api/ingest/v1/telemetry"
        pending: list[dict] = []

        with httpx.Client(timeout=config.timeout_seconds, verify=config.verify_tls) as client:
            while not self._stop.is_set():
                pending.append(self.collect())
                if len(pending) >= config.batch_size:
                    pending = self._flush(client, endpoint, headers, pending, config)
                self._stop.wait(config.interval_seconds)

            if pending:
                self._flush(client, endpoint, headers, pending, config)

    @staticmethod
    def _flush(
        client: httpx.Client,
        endpoint: str,
        headers: dict[str, str],
        events: list[dict],
        config: AgentConfig,
    ) -> list[dict]:
        delay = 1.0
        remaining = events
        while remaining:
            try:
                response = client.post(
                    endpoint,
                    json={"events": remaining, "agent_version": config.agent_version},
                    headers=headers,
                )
                if response.status_code == 401:
                    raise RuntimeError("Telemetry API key was rejected; refusing to retry")
                response.raise_for_status()
                return []
            except RuntimeError:
                raise
            except httpx.HTTPError:
                time.sleep(min(delay, 8.0))
                delay = min(delay * 2, 8.0)
        return remaining


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and ship host telemetry")
    parser.add_argument("--check", action="store_true", help="Collect one sample and print it")
    return parser.parse_args()


def main() -> int:
    config = AgentConfig.from_env()
    collector = HostCollector(config.probe_url)
    signal.signal(signal.SIGINT, lambda *_: collector.stop())
    signal.signal(signal.SIGTERM, lambda *_: collector.stop())

    args = parse_args()
    if args.check:
        print(collector.collect())
        return 0

    collector.run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
