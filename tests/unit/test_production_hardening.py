"""Unit tests for dependency-free production hardening and the host agent."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from agent.telemetry_agent import HostCollector
from app.core.rate_limit import RateLimitExceeded, SlidingWindowRateLimiter


def test_rate_limiter_returns_retry_after_and_can_clear() -> None:
    limiter = SlidingWindowRateLimiter()
    limiter.check("ip", limit=1, window_seconds=60)

    try:
        limiter.check("ip", limit=1, window_seconds=60)
    except RateLimitExceeded as exc:
        assert exc.retry_after >= 1
    else:
        raise AssertionError("expected rate limit")

    limiter.clear("ip")
    limiter.check("ip", limit=1, window_seconds=60)


def test_agent_collects_real_host_metrics_without_probe() -> None:
    collector = HostCollector(None)
    sample = collector.collect()

    assert sample["sequence"] > 0
    assert 0 <= sample["cpu"] <= 100
    assert 0 <= sample["memory"] <= 100
    assert sample["network_mbps"] >= 0
    assert sample["requests_per_second"] == 0.0
    assert sample["error_rate"] == 0.0
    assert sample["latency_ms"] == 0.0


def test_agent_flush_sends_current_batch() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.is_success = True
    client.post.return_value = response

    config = type("Config", (), {"agent_version": "test/1.0"})()
    events = [{
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sequence": 1,
        "cpu": 10,
        "memory": 20,
        "temperature": 30,
        "network_mbps": 1,
        "requests_per_second": 2,
        "error_rate": 0,
        "latency_ms": 3,
    }]

    remaining = HostCollector._flush(
        client,
        "http://example/api/ingest/v1/telemetry",
        {"X-Telemetry-Key": "secret"},
        events,
        config,
    )

    assert remaining == []
    sent = client.post.call_args.kwargs["json"]
    assert sent["events"] == events
    assert sent["agent_version"] == "test/1.0"


def test_agent_flush_does_not_retry_authentication_failure() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 401
    client.post.return_value = response

    config = type("Config", (), {"agent_version": "test/1.0"})()
    events = [{"sequence": 1}]

    try:
        HostCollector._flush(
            client,
            "http://example/api/ingest/v1/telemetry",
            {"X-Telemetry-Key": "secret"},
            events,
            config,
        )
    except RuntimeError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("authentication failures must not be retried")
