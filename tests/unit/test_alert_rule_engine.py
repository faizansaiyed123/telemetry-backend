"""Unit tests for stateful alert rule evaluation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.alerts import Severity
from app.services.alert_rule_engine import AlertRuleEngine


def rule(*, duration_seconds: float = 0.0):
    return SimpleNamespace(
        id="rule-1",
        name="High CPU",
        metric="cpu",
        operator=">",
        threshold=80.0,
        duration_seconds=duration_seconds,
        cooldown_seconds=300.0,
        severity="CRITICAL",
        enabled=True,
    )


def event(cpu: float, timestamp: datetime):
    return SimpleNamespace(
        cpu=cpu,
        timestamp=timestamp,
        host_id="host-1",
    )


def test_rule_requires_duration_before_firing() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule(duration_seconds=10)])

    t0 = datetime.now(timezone.utc)
    assert engine.evaluate(event(90, t0)) == []
    transitions = engine.evaluate(event(91, t0 + timedelta(seconds=10)))

    assert len(transitions) == 1
    assert transitions[0].action == "created"
    assert transitions[0].alert.severity == Severity.CRITICAL


def test_rule_resolves_when_condition_clears() -> None:
    engine = AlertRuleEngine()
    engine.load_rules([rule()])

    t0 = datetime.now(timezone.utc)
    created = engine.evaluate(event(90, t0))
    assert created[0].action == "created"

    resolved = engine.evaluate(event(70, t0 + timedelta(seconds=1)))
    assert len(resolved) == 1
    assert resolved[0].action == "resolved"
    assert resolved[0].alert.resolved is True


def test_disabled_rule_is_ignored() -> None:
    disabled = rule()
    disabled.enabled = False

    engine = AlertRuleEngine()
    engine.load_rules([disabled])

    assert engine.evaluate(event(100, datetime.now(timezone.utc))) == []
    assert engine.evaluations == 0
