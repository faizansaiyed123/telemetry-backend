"""Unit tests for the dependency-light host telemetry agent."""

from unittest.mock import MagicMock, patch

from app.agent import AgentConfig, TelemetryAgent


def test_collect_event_has_bounded_host_metrics():
    config = AgentConfig(api_url="http://test", api_key="secret", batch_size=1)
    counters = MagicMock(bytes_sent=1000, bytes_recv=2000)

    with patch("app.agent.psutil.cpu_percent", return_value=25.5),         patch("app.agent.psutil.virtual_memory", return_value=MagicMock(percent=55.5)),         patch("app.agent.psutil.net_io_counters", side_effect=[counters, MagicMock(bytes_sent=2000, bytes_recv=4000)]),         patch("app.agent.psutil.sensors_temperatures", return_value={}):
        agent = TelemetryAgent(config)
        event = agent.collect_event()

    assert event["sequence"] == 1
    assert 0 <= event["cpu"] <= 100
    assert 0 <= event["memory"] <= 100
    assert event["temperature"] == 0.0
    assert event["requests_per_second"] == 0
    assert event["error_rate"] == 0
    assert event["latency_ms"] == 0


def test_send_events_uses_api_key_and_batch_contract():
    config = AgentConfig(api_url="http://test", api_key="secret", batch_size=1)
    client = MagicMock()
    client.post.return_value = MagicMock(
        status_code=202,
        json=lambda: {"status": "accepted"},
    )
    agent = TelemetryAgent(config, client=client)

    result = agent.send_events([{"sequence": 1}])

    client.post.assert_called_once()
    call = client.post.call_args
    assert call.kwargs["headers"] == {"X-API-Key": "secret"}
    assert call.kwargs["json"]["events"] == [{"sequence": 1}]
    assert result["status"] == "accepted"
