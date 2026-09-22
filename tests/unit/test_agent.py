"""Unit tests for the telemetry agent collector and batch client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.client import TelemetryAgent
from agent.collector import AgentSample, SystemTelemetryCollector


def test_collector_calculates_network_rate() -> None:
    net_before = MagicMock(bytes_sent=1000, bytes_recv=2000)
    net_after = MagicMock(bytes_sent=11000, bytes_recv=22000)

    with (
        patch("agent.collector.psutil.net_io_counters", side_effect=[net_before, net_after]),
        patch("agent.collector.psutil.cpu_percent", side_effect=[0.0, 25.5]),
        patch("agent.collector.psutil.virtual_memory", return_value=MagicMock(percent=60.2)),
        patch("agent.collector.psutil.sensors_temperatures", return_value={}),
        patch("agent.collector.time.monotonic", side_effect=[100.0, 102.0]),
        patch("agent.collector.time.time", return_value=1700000000.0),
        patch("agent.collector.time.time_ns", return_value=1700000000000000000),
    ):
        collector = SystemTelemetryCollector()
        sample = collector.sample()

    assert sample.cpu == 25.5
    assert sample.memory == 60.2
    assert sample.network_mbps == 0.12
    assert sample.temperature == 0.0
    assert sample.sequence == 1700000000000


@pytest.mark.asyncio
async def test_agent_retries_transient_server_failure() -> None:
    response_500 = httpx.Response(500, request=httpx.Request("POST", "http://test"))
    response_ok = httpx.Response(200, request=httpx.Request("POST", "http://test"))

    client = MagicMock()
    client.post = AsyncMock(side_effect=[response_500, response_ok])

    agent = TelemetryAgent(base_url="http://test", api_key="secret", batch_size=1)
    agent._buffer = [
        AgentSample(
            timestamp=1700000000.0,
            sequence=1700000000000,
            cpu=10,
            memory=20,
            temperature=0,
            network_mbps=1,
            requests_per_second=0,
            error_rate=0,
            latency_ms=0,
        )
    ]

    with patch("agent.client.asyncio.sleep", new=AsyncMock()) as sleep:
        await agent.flush(client)

    assert client.post.await_count == 2
    assert agent._buffer == []
    assert sleep.await_count == 1


@pytest.mark.asyncio
async def test_agent_stops_on_rejected_credentials() -> None:
    response = httpx.Response(401, request=httpx.Request("POST", "http://test"))
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    agent = TelemetryAgent(base_url="http://test", api_key="revoked", batch_size=1)
    agent._buffer = [
        AgentSample(
            timestamp=1700000000.0,
            sequence=1700000000000,
            cpu=10,
            memory=20,
            temperature=0,
            network_mbps=1,
            requests_per_second=0,
            error_rate=0,
            latency_ms=0,
        )
    ]

    with pytest.raises(RuntimeError, match="API key rejected"):
        await agent.flush(client)


