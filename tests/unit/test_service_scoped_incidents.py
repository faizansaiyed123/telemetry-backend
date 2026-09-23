"""Unit tests for service-scoped incident correlation."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.alerts import Alert, Severity
from app.services.incident_engine import IncidentEngine


def _alert(alert_id: str, service_id: str) -> Alert:
    return Alert(
        id=alert_id,
        timestamp=datetime.now(timezone.utc),
        metric="latency_ms",
        value=250.0,
        baseline=100.0,
        severity=Severity.CRITICAL,
        message="test",
        host_id="host-1",
        service_id=service_id,
    )


def test_incidents_do_not_merge_different_services_on_same_host() -> None:
    engine = IncidentEngine()
    first = engine.on_alert_created(_alert("a1", "service-a"))
    second = engine.on_alert_created(_alert("a2", "service-b"))

    assert first.id != second.id
    assert first.service_id == "service-a"
    assert second.service_id == "service-b"


def test_incidents_merge_same_service_and_host_within_window() -> None:
    engine = IncidentEngine()
    first = engine.on_alert_created(_alert("a1", "service-a"))
    second = engine.on_alert_created(_alert("a2", "service-a"))

    assert first.id == second.id
    assert second.active_alert_ids == {"a1", "a2"}
