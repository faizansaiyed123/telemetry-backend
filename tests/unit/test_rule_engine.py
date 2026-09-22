"""Unit tests for stateful alert rules."""

from datetime import timedelta

from app.models.observability import AlertRuleRecord
from app.models.telemetry import TelemetryEvent
from app.services.rule_engine import RuleEngine
from app.utils.time import utc_now


def make_event(timestamp, cpu=50.0, host_id="host-1"):
    return TelemetryEvent(
        timestamp=timestamp,
        sequence=1,
        cpu=cpu,
        memory=40,
        temperature=45,
        network_mbps=100,
        requests_per_second=500,
        error_rate=0.5,
        latency_ms=30,
        host_id=host_id,
        source="agent",
    )


def make_rule(**overrides):
    data = {
        "id": "rule-1",
        "name": "High CPU",
        "metric": "cpu",
        "operator": "gt",
        "threshold": 80,
        "duration_seconds": 2,
        "severity": "CRITICAL",
        "enabled": True,
        "cooldown_seconds": 10,
    }
    data.update(overrides)
    return AlertRuleRecord(**data)


def test_rule_fires_only_after_sustained_duration():
    now = utc_now()
    engine = RuleEngine()
    engine.load([make_rule()])

    assert engine.evaluate(make_event(now, cpu=90)) == []
    assert engine.evaluate(make_event(now + timedelta(seconds=1), cpu=91)) == []

    actions = engine.evaluate(make_event(now + timedelta(seconds=2), cpu=92))
    assert len(actions) == 1
    assert actions[0].action == "fire"
    assert actions[0].rule.id == "rule-1"
    assert actions[0].alert_id


def test_rule_resolves_when_condition_clears():
    now = utc_now()
    engine = RuleEngine()
    engine.load([make_rule(duration_seconds=0)])

    fired = engine.evaluate(make_event(now, cpu=90))
    assert fired[0].action == "fire"

    actions = engine.evaluate(make_event(now + timedelta(seconds=1), cpu=70))
    assert len(actions) == 1
    assert actions[0].action == "resolve"
    assert actions[0].alert_id == fired[0].alert_id


def test_rule_cooldown_prevents_immediate_refire():
    now = utc_now()
    engine = RuleEngine()
    engine.load([make_rule(duration_seconds=0, cooldown_seconds=30)])

    fired = engine.evaluate(make_event(now, cpu=90))
    assert fired[0].action == "fire"
    engine.evaluate(make_event(now + timedelta(seconds=1), cpu=70))

    assert engine.evaluate(make_event(now + timedelta(seconds=2), cpu=90)) == []
    actions = engine.evaluate(make_event(now + timedelta(seconds=31), cpu=90))
    assert actions[0].action == "fire"


def test_host_scoped_rule_ignores_other_hosts():
    now = utc_now()
    engine = RuleEngine()
    engine.load([make_rule(duration_seconds=0, host_id="host-1")])

    assert engine.evaluate(make_event(now, cpu=95, host_id="host-2")) == []
    assert engine.evaluate(make_event(now + timedelta(seconds=1), cpu=95, host_id="host-1"))[0].action == "fire"
