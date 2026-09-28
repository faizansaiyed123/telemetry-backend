"""Unit tests for the local telemetry agent."""

from agent.telemetry_agent import AgentConfig, HostCollector


def test_agent_config_from_env(monkeypatch):
    monkeypatch.setenv("TELEMETRY_API_URL", "https://example.test")
    monkeypatch.setenv("TELEMETRY_API_KEY", "secret")
    monkeypatch.setenv("TELEMETRY_AGENT_INTERVAL", "3")
    monkeypatch.setenv("TELEMETRY_AGENT_BATCH_SIZE", "20")
    monkeypatch.setenv("TELEMETRY_PROBE_URL", "https://service.example/health")
    monkeypatch.setenv("TELEMETRY_VERIFY_TLS", "false")
    monkeypatch.setenv("TELEMETRY_AGENT_TIMEOUT", "15")
    config = AgentConfig.from_env()
    assert config.api_url == "https://example.test"
    assert config.api_key == "secret"
    assert config.interval_seconds == 3.0
    assert config.batch_size == 20
    assert config.probe_url == "https://service.example/health"
    assert config.verify_tls is False
    assert config.timeout_seconds == 15.0


def test_agent_requires_credentials(monkeypatch):
    monkeypatch.delenv("TELEMETRY_API_URL", raising=False)
    monkeypatch.delenv("TELEMETRY_API_KEY", raising=False)
    try:
        AgentConfig.from_env()
    except ValueError as exc:
        assert "TELEMETRY_API_URL" in str(exc)
    else:
        raise AssertionError("expected missing API URL to fail")


def test_host_collector_emits_valid_bounded_sample():
    collector = HostCollector(None)
    sample = collector.collect()
    assert sample["sequence"] > 0
    assert 0 <= sample["cpu"] <= 100
    assert 0 <= sample["memory"] <= 100
    assert 0 <= sample["temperature"] <= 120
    assert sample["network_mbps"] >= 0
    assert sample["requests_per_second"] == 0
    assert sample["error_rate"] == 0
    assert sample["latency_ms"] == 0


def test_host_collector_sequence_is_restart_safe():
    collector = HostCollector(None)
    first = collector.collect()
    second = collector.collect()
    assert second["sequence"] > first["sequence"]


def test_agent_check_mode_does_not_require_credentials(monkeypatch):
    monkeypatch.setattr("sys.argv", ["telemetry-agent", "--check"])
    from agent.telemetry_agent import main
    assert main() == 0


def test_probe_honors_tls_verification_and_timeout():
    from unittest.mock import MagicMock, patch

    collector = HostCollector("https://service.example/health")
    response = MagicMock()
    response.is_success = True

    with patch("agent.telemetry_agent.httpx.get", return_value=response) as get:
        _, error_rate, latency_ms = collector._probe(verify_tls=False, timeout_seconds=15.0)

    get.assert_called_once_with(
        "https://service.example/health",
        timeout=15.0,
        verify=False,
    )
    assert error_rate == 0.0
    assert latency_ms >= 0.0


def test_flush_retries_server_error_and_preserves_no_duplicate_batch():
    import httpx
    from unittest.mock import MagicMock, patch

    response_500 = httpx.Response(503, request=httpx.Request("POST", "https://example.test"))
    response_200 = httpx.Response(200, request=httpx.Request("POST", "https://example.test"))
    client = MagicMock()
    client.post.side_effect = [response_500, response_200]

    events = [HostCollector(None).collect()]
    config = AgentConfig(api_url="https://example.test", api_key="secret")

    with patch("agent.telemetry_agent.time.sleep") as sleep:
        remaining = HostCollector._flush(
            client,
            "https://example.test/api/ingest/v1/telemetry",
            {"X-Telemetry-Key": "secret"},
            events,
            config,
        )

    assert remaining == []
    assert client.post.call_count == 2
    sleep.assert_called_once()
