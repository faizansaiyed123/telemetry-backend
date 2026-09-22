"""Unit tests for alert-to-incident correlation."""

from datetime import timedelta

from app.models.alerts import Alert, Severity
from app.services.incident_engine import IncidentEngine
from app.utils.time import utc_now


def alert(alert_id: str, host_id: str, timestamp, severity=Severity.WARNING) -> Alert:
    return Alert(
        id=alert_id,
        timestamp=timestamp,
        metric="cpu",
        value=90.0,
        baseline=80.0,
        severity=severity,
        message="test",
        host_id=host_id,
        source="rule",
        rule_id="rule-1",
    )


def test_related_alerts_share_one_incident_and_escalate_severity():
    engine = IncidentEngine()
    now = utc_now()

    first = engine.on_alert_created(alert("a1", "host-1", now, Severity.WARNING))
    second = engine.on_alert_created(
        alert("a2", "host-1", now + timedelta(seconds=30), Severity.CRITICAL)
    )

    assert first.id == second.id
    assert second.severity == "CRITICAL"
    assert second.alert_ids == {"a1", "a2"}
    assert second.active_alert_ids == {"a1", "a2"}
    assert len(engine.all()) == 1


def test_different_hosts_create_separate_incidents():
    engine = IncidentEngine()
    now = utc_now()

    one = engine.on_alert_created(alert("a1", "host-1", now))
    two = engine.on_alert_created(alert("a2", "host-2", now))

    assert one.id != two.id
    assert len(engine.all()) == 2


def test_incident_stays_open_until_all_alerts_resolve():
    engine = IncidentEngine()
    now = utc_now()

    first = engine.on_alert_created(alert("a1", "host-1", now))
    second = engine.on_alert_created(alert("a2", "host-1", now + timedelta(seconds=1)))

    first_resolved = alert("a1", "host-1", now + timedelta(seconds=2))
    first_resolved.resolved = True
    first_resolved.resolved_at = first_resolved.timestamp
    first_resolved.incident_id = first.id
    engine.on_alert_resolved(first_resolved)

    assert second.status == "open"
    assert second.active_alert_ids == {"a2"}

    second_resolved = alert("a2", "host-1", now + timedelta(seconds=3))
    second_resolved.resolved = True
    second_resolved.resolved_at = second_resolved.timestamp
    second_resolved.incident_id = second.id
    engine.on_alert_resolved(second_resolved)

    assert second.status == "resolved"
    assert second.resolved_at == second_resolved.timestamp


def test_acknowledged_incident_reopens_on_new_alert():
    engine = IncidentEngine()
    now = utc_now()

    first = engine.on_alert_created(alert("a1", "host-1", now))
    acknowledged = engine.acknowledge(first.id)
    assert acknowledged is not None
    assert acknowledged.status == "acknowledged"

    reopened = engine.on_alert_created(
        alert("a2", "host-1", now + timedelta(seconds=30), Severity.WARNING)
    )
    assert reopened.id == first.id
    assert reopened.status == "open"
