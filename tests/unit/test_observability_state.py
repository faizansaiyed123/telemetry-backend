"""Unit coverage for production observability state machines."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.telemetry import TelemetryEvent
from app.services.alert_rule_engine import AlertRuleEngine
from app.services.anomaly_detector import AnomalyDetector


def make_event(ts: datetime, *, host_id: str, sequence: int, cpu: float) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=ts,
        sequence=sequence,
        cpu=cpu,
        memory=50,
        temperature=45,
        network_mbps=10,
        requests_per_second=100,
        error_rate=0.1,
        latency_ms=20,
        host_id=host_id,
        source="agent",
    )


def test_rule_engine_enforces_duration_and_resolves() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = SimpleNamespace(
        id="rule-1",
        name="High CPU",
        metric="cpu",
        operator=">=",
        threshold=80.0,
        duration_seconds=5.0,
        cooldown_seconds=30.0,
        severity="CRITICAL",
        enabled=True,
    )
    engine = AlertRuleEngine()
    engine.load_rules([rule])

    assert engine.evaluate(make_event(base, host_id="h1", sequence=1, cpu=90)) == []
    assert engine.evaluate(make_event(base + timedelta(seconds=4), host_id="h1", sequence=2, cpu=91)) == []

    created = engine.evaluate(make_event(base + timedelta(seconds=5), host_id="h1", sequence=3, cpu=92))
    assert len(created) == 1
    assert created[0].action == "created"

    assert engine.evaluate(make_event(base + timedelta(seconds=6), host_id="h1", sequence=4, cpu=95)) == []

    resolved = engine.evaluate(make_event(base + timedelta(seconds=7), host_id="h1", sequence=5, cpu=40))
    assert len(resolved) == 1
    assert resolved[0].action == "resolved"
    assert resolved[0].alert.id == created[0].alert.id


def test_rule_engine_keeps_state_separate_per_host() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = SimpleNamespace(
        id="rule-2",
        name="CPU",
        metric="cpu",
        operator=">",
        threshold=80.0,
        duration_seconds=0,
        cooldown_seconds=0,
        severity="WARNING",
        enabled=True,
    )
    engine = AlertRuleEngine()
    engine.load_rules([rule])

    first = engine.evaluate(make_event(base, host_id="host-a", sequence=1, cpu=90))
    second = engine.evaluate(make_event(base, host_id="host-b", sequence=1, cpu=90))

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].alert.id != second[0].alert.id


def test_anomaly_detector_keeps_rolling_baselines_per_host() -> None:
    detector = AnomalyDetector(threshold=3.0, min_history=3, window_size=5)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for idx, cpu in enumerate([10, 11, 10], start=1):
        detector.update(make_event(base + timedelta(seconds=idx), host_id="host-a", sequence=idx, cpu=cpu))
    for idx, cpu in enumerate([80, 81, 80], start=1):
        detector.update(make_event(base + timedelta(seconds=idx), host_id="host-b", sequence=idx, cpu=cpu))

    a_result = detector.update(make_event(base + timedelta(seconds=10), host_id="host-a", sequence=10, cpu=10))
    b_result = detector.update(make_event(base + timedelta(seconds=10), host_id="host-b", sequence=10, cpu=80))

    a_cpu = next(item for item in a_result if item.metric == "cpu")
    b_cpu = next(item for item in b_result if item.metric == "cpu")
    assert a_cpu.is_anomaly is False
    assert b_cpu.is_anomaly is False
