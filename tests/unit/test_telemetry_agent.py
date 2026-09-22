"""Unit tests for the local telemetry agent."""

from agent.telemetry_agent import AgentConfig, HostCollector


def test_agent_config_from_env(monkeypatch):
    monkeypatch.setenv("TELEMETRY_API_URL", "https://example.test")
    monkeypatch.setenv("TELEMETRY_API_KEY", "secret")
    monkeypatch.setenv("TELEMETRY_AGENT_INTERVAL", "3")
    monkeypatch.setenv("TELEMETRY_AGENT_BATCH_SIZE", "20")
    config = AgentConfig.from_env()
    assert config.api_url == "https://example.test"
    assert config.api_key == "secret"
    assert config.interval_seconds == 3.0
    assert config.batch_size == 20


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
    assert sample["sequence"] == 1
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
