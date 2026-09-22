"""Tests for the zero-cost host telemetry agent."""

from agent.telemetry_agent import AgentConfig, HostCollector


def test_agent_collects_a_schema_valid_sample():
    sample = HostCollector(None).collect()

    assert set(sample) == {
        "timestamp",
        "sequence",
        "cpu",
        "memory",
        "temperature",
        "network_mbps",
        "requests_per_second",
        "error_rate",
        "latency_ms",
    }
    assert 0 <= sample["cpu"] <= 100
    assert 0 <= sample["memory"] <= 100
    assert sample["sequence"] > 0


def test_agent_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("TELEMETRY_API_URL", raising=False)
    monkeypatch.delenv("TELEMETRY_API_KEY", raising=False)

    try:
        AgentConfig.from_env()
    except ValueError as exc:
        assert "TELEMETRY_API_URL" in str(exc)
    else:
        raise AssertionError("missing credentials should be rejected")
