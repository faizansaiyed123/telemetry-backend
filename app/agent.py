"""Small dependency-light host telemetry agent for the Telemetry ingestion API."""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import psutil

logger = logging.getLogger("telemetry-agent")
AGENT_VERSION = "1.0.0"


@dataclass(slots=True)
class AgentConfig:
    api_url: str
    api_key: str
    interval_seconds: float = 1.0
    batch_size: int = 10
    timeout_seconds: float = 10.0


class TelemetryAgent:
    """Collect host metrics and push batches to the backend."""

    def __init__(self, config: AgentConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout_seconds)
        self._sequence = 0
        self._last_network = psutil.net_io_counters()
        self._last_network_at = time.monotonic()

    def collect_event(self) -> dict[str, object]:
        self._sequence += 1
        cpu = float(psutil.cpu_percent(interval=0.05))
        memory = float(psutil.virtual_memory().percent)

        now = time.monotonic()
        network = psutil.net_io_counters()
        elapsed = max(now - self._last_network_at, 0.001)
        bytes_delta = max(
            (network.bytes_sent + network.bytes_recv)
            - (self._last_network.bytes_sent + self._last_network.bytes_recv),
            0,
        )
        network_mbps = (bytes_delta * 8) / elapsed / 1_000_000
        self._last_network = network
        self._last_network_at = now

        temperature = self._read_temperature()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self._sequence,
            "cpu": round(max(0.0, min(cpu, 100.0)), 2),
            "memory": round(max(0.0, min(memory, 100.0)), 2),
            "temperature": round(max(0.0, min(temperature, 120.0)), 2),
            "network_mbps": round(max(0.0, min(network_mbps, 10_000.0)), 2),
            # Application-level metrics require instrumentation in the target
            # service. They default to zero rather than inventing fake values.
            "requests_per_second": 0,
            "error_rate": 0,
            "latency_ms": 0,
        }

    @staticmethod
    def _read_temperature() -> float:
        try:
            sensors = psutil.sensors_temperatures()
        except (AttributeError, RuntimeError):
            return 0.0

        readings = [
            float(entry.current)
            for entries in sensors.values()
            for entry in entries
            if getattr(entry, "current", None) is not None
        ]
        return sum(readings) / len(readings) if readings else 0.0

    def send_events(self, events: list[dict[str, object]]) -> dict:
        response = self.client.post(
            self.config.api_url.rstrip("/") + "/api/ingest/telemetry",
            headers={"X-API-Key": self.config.api_key},
            json={"events": events, "agent_version": AGENT_VERSION},
        )
        response.raise_for_status()
        return response.json()

    def run_once(self) -> dict:
        events = [self.collect_event() for _ in range(self.config.batch_size)]
        return self.send_events(events)

    def run_forever(self) -> None:
        logger.info("Telemetry agent started for %s", self.config.api_url)
        try:
            while True:
                started = time.monotonic()
                try:
                    result = self.run_once()
                    logger.info(
                        "sent=%s accepted=%s persisted_queue=%s dropped=%s",
                        result.get("received"),
                        result.get("accepted"),
                        result.get("queued_for_persistence"),
                        result.get("dropped_from_persistence"),
                    )
                except httpx.HTTPStatusError as exc:
                    logger.error("ingestion failed with HTTP %s: %s", exc.response.status_code, exc.response.text)
                    if exc.response.status_code in {401, 403}:
                        raise
                except httpx.HTTPError as exc:
                    logger.warning("network error while sending telemetry: %s", exc)

                remaining = self.config.interval_seconds - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            if self.client is not None:
                self.client.close()


def config_from_env() -> AgentConfig:
    api_url = os.getenv("TELEMETRY_API_URL", "").strip()
    api_key = os.getenv("TELEMETRY_API_KEY", "").strip()
    if not api_url or not api_key:
        raise ValueError("TELEMETRY_API_URL and TELEMETRY_API_KEY are required")

    return AgentConfig(
        api_url=api_url,
        api_key=api_key,
        interval_seconds=float(os.getenv("TELEMETRY_AGENT_INTERVAL_SECONDS", "1")),
        batch_size=max(1, int(os.getenv("TELEMETRY_AGENT_BATCH_SIZE", "10"))),
        timeout_seconds=max(1.0, float(os.getenv("TELEMETRY_AGENT_TIMEOUT_SECONDS", "10"))),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Send host metrics to a Telemetry backend")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()

    config = config_from_env()
    if args.api_url:
        config.api_url = args.api_url
    if args.api_key:
        config.api_key = args.api_key
    if args.interval is not None:
        config.interval_seconds = max(0.1, args.interval)
    if args.batch_size is not None:
        config.batch_size = max(1, args.batch_size)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TelemetryAgent(config).run_forever()


if __name__ == "__main__":
    main()
