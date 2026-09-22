"""Reliable HTTP client for the telemetry ingestion API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

import httpx

from app.models.observability import IngestTelemetryBatch, IngestTelemetryEvent
from .collector import AgentSample, SystemTelemetryCollector

logger = logging.getLogger(__name__)


class TelemetryAgent:
    """Collect and ship telemetry in bounded batches with retry/backoff."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        agent_version: str = "1.0.0",
        interval_seconds: float = 2.0,
        batch_size: int = 10,
        requests_per_second: float = 0.0,
        error_rate: float = 0.0,
        latency_ms: float = 0.0,
        temperature_fallback: float = 0.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if not 1 <= batch_size <= 250:
            raise ValueError("batch_size must be between 1 and 250")

        self.endpoint = base_url.rstrip("/") + "/api/ingest/v1/telemetry"
        self.api_key = api_key
        self.agent_version = agent_version
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.collector = SystemTelemetryCollector(
            requests_per_second=requests_per_second,
            error_rate=error_rate,
            latency_ms=latency_ms,
            temperature_fallback=temperature_fallback,
        )
        self._http_client = http_client
        self._owns_client = http_client is None
        self._buffer: list[AgentSample] = []

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until stop_event is set."""
        client = self._http_client or httpx.AsyncClient(timeout=10.0)
        try:
            while not stop_event.is_set():
                self._buffer.append(self.collector.sample())
                if len(self._buffer) >= self.batch_size:
                    await self.flush(client)

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    if self._buffer:
                        await self.flush(client)
        finally:
            if self._buffer:
                try:
                    await self.flush(client)
                except Exception:
                    logger.exception("Failed to flush final telemetry batch")
            if self._owns_client:
                await client.aclose()

    async def flush(self, client: httpx.AsyncClient | None = None) -> None:
        """Send the current batch, retrying transient failures."""
        if not self._buffer:
            return

        owns_client = False
        if client is None:
            client = self._http_client or httpx.AsyncClient(timeout=10.0)
            owns_client = self._http_client is None

        samples = self._buffer.copy()
        payload = IngestTelemetryBatch(
            agent_version=self.agent_version,
            events=[
                IngestTelemetryEvent(
                    timestamp=sample.timestamp,
                    sequence=sample.sequence,
                    cpu=sample.cpu,
                    memory=sample.memory,
                    temperature=sample.temperature,
                    network_mbps=sample.network_mbps,
                    requests_per_second=sample.requests_per_second,
                    error_rate=sample.error_rate,
                    latency_ms=sample.latency_ms,
                )
                for sample in samples
            ],
        )

        try:
            delay = 0.5
            for attempt in range(5):
                try:
                    response = await client.post(
                        self.endpoint,
                        headers={"X-Telemetry-Key": self.api_key},
                        json=payload.model_dump(mode="json"),
                    )
                except httpx.HTTPError as exc:
                    if attempt == 4:
                        raise RuntimeError("Telemetry endpoint unavailable") from exc
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue

                if response.status_code in {401, 403}:
                    raise RuntimeError("Telemetry API key rejected; stopping agent")

                if response.status_code == 429:
                    if attempt == 4:
                        raise RuntimeError("Telemetry ingestion rate limited repeatedly")
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    await asyncio.sleep(min(max(retry_after, 0.1), 30.0))
                    continue

                if 500 <= response.status_code < 600:
                    if attempt == 4:
                        response.raise_for_status()
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue

                response.raise_for_status()
                self._buffer = [item for item in self._buffer if item not in samples]
                return

            raise RuntimeError("Telemetry batch was not accepted")
        finally:
            if owns_client:
                await client.aclose()


__all__ = ["TelemetryAgent", "AgentSample", "SystemTelemetryCollector"]
