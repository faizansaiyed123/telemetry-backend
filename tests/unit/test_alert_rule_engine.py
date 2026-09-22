"""Behavioral tests for stateful alert rule evaluation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.alerts import Severity
from app.models.db import AlertRule
from app.models.telemetry import TelemetryEvent
from app.services.alert_rule_engine import AlertRuleEngine


def event(at: datetime, *, host_id: str, cpu: float, sequence: int) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=at,
        sequence=sequence,
        cpu=cpu,
        memory=50,
        temperature=45,
        network_mbps=100,
        requests_per_second=100,
        error_rate=0.2,
        latency_ms=30,
        host_id=host_id,
        source="agent",
    )


def rule(*, duration: float = 0, cooldown: float = 60) -> AlertRule:
    return AlertRule(
        id="rule-1",
        name="High CPU",
        metric="cpu",
        operator=">=",
        threshold=80,
        duration_seconds=duration,
        cooldown_seconds=cooldown,
        severity="CRITICAL",
        enabled=True,
    )


def test_duration_requires_sustained_condition() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule(duration=10)])
    base = datetime.now(timezone.utc)

    assert engine.evaluate(event(base, host_id="host-a", cpu=90, sequence=1)) == []
    assert engine.evaluate(event(base + timedelta(seconds=9), host_id="host-a", cpu=90, sequence=2)) == []

    transitions = engine.evaluate(event(base + timedelta(seconds=10), host_id="host-a", cpu=90, sequence=3))
    assert len(transitions) == 1
    assert transitions[0].action == "created"
    assert transitions[0].alert.severity == Severity.CRITICAL


def test_condition_clear_resolves_active_alert() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule()])
    base = datetime.now(timezone.utc)

    created = engine.evaluate(event(base, host_id="host-a", cpu=90, sequence=1))
    assert created and created[0].action == "created"

    resolved = engine.evaluate(event(base + timedelta(seconds=1), host_id="host-a", cpu=40, sequence=2))
    assert resolved and resolved[0].action == "resolved"
    assert resolved[0].alert.resolved is True


def test_rule_state_is_isolated_per_host() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule(duration=5)])
    base = datetime.now(timezone.utc)

    assert engine.evaluate(event(base, host_id="host-a", cpu=95, sequence=1)) == []
    assert engine.evaluate(event(base + timedelta(seconds=5), host_id="host-a", cpu=95, sequence=2))
    assert engine.evaluate(event(base + timedelta(seconds=5), host_id="host-b", cpu=95, sequence=1)) == []


def test_disabled_rule_does_not_evaluate() -> None:
    disabled = rule()
    disabled.enabled = False
    engine = AlertRuleEngine()
    engine.load_rules([disabled])

    assert engine.evaluate(event(datetime.now(timezone.utc), host_id="host-a", cpu=100, sequence=1)) == []
    assert engine.evaluations == 0


def test_reload_preserves_rule_set_but_removes_deleted_rule_state() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule()])
    now = datetime.now(timezone.utc)
    assert engine.evaluate(event(now, host_id="host-a", cpu=90, sequence=1))

    replacement = SimpleNamespace(
        id="rule-2",
        name="Low CPU",
        metric="cpu",
        operator="<",
        threshold=20.0,
        duration_seconds=0.0,
        cooldown_seconds=60.0,
        severity="WARNING",
        enabled=True,
    )
    engine.load_rules([replacement])
    assert engine.evaluate(event(now + timedelta(seconds=1), host_id="host-a", cpu=10, sequence=2))
