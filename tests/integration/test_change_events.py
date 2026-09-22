"""Integration tests for change-event correlation and incident evidence."""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import create_app
from app.models.alerts import Alert, Severity
from app.models.db import AlertRecord
from app.utils.time import utc_now


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            response = await ac.post(
                "/api/auth/login",
                data={
                    "username": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert response.status_code == 200, response.text
            ac.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
            yield ac


@pytest.mark.asyncio
async def test_create_and_list_change_event(client: AsyncClient) -> None:
    hosts = await client.get("/api/hosts")
    assert hosts.status_code == 200, hosts.text
    host_id = hosts.json()["hosts"][0]["id"]

    occurred_at = utc_now() - timedelta(minutes=2)
    response = await client.post(
        "/api/changes",
        json={
            "event_type": "deployment",
            "title": "Release 2026.09.22",
            "description": "Promoted API release",
            "host_id": host_id,
            "source": "ci",
            "external_ref": "build-8421",
            "occurred_at": occurred_at.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["host_id"] == host_id
    assert created["event_type"] == "deployment"
    assert created["source"] == "ci"
    assert created["external_ref"] == "build-8421"

    listed = await client.get("/api/changes", params={"host_id": host_id})
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == created["id"] for item in listed.json())


@pytest.mark.asyncio
async def test_change_event_rejects_inactive_or_unknown_host(client: AsyncClient) -> None:
    unknown = await client.post(
        "/api/changes",
        json={"title": "Invalid host change", "host_id": "does-not-exist"},
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_incident_evidence_contains_alert_and_nearby_change(client: AsyncClient) -> None:
    manager = client._transport.app.state.telemetry_manager
    host_id = manager.persistence_host_id
    assert host_id is not None

    timestamp = utc_now() - timedelta(minutes=5)
    alert = Alert(
        id="evidence-alert-1",
        timestamp=timestamp,
        metric="latency_ms",
        value=750.0,
        baseline=100.0,
        severity=Severity.CRITICAL,
        message="latency_ms exceeded SLO threshold",
        host_id=host_id,
        source="rule",
        rule_id="rule-evidence",
    )
    incident = manager.incident_engine.on_alert_created(alert)

    with SessionLocal() as db:
        db.add(
            AlertRecord(
                id=alert.id,
                host_id=host_id,
                metric=alert.metric,
                value=alert.value,
                baseline=alert.baseline,
                severity=alert.severity.value,
                message=alert.message,
                status="active",
                acknowledged=False,
                source=alert.source,
                rule_id=alert.rule_id,
                timestamp=alert.timestamp,
            )
        )
        db.commit()

    change = await client.post(
        "/api/changes",
        json={
            "event_type": "deployment",
            "title": "Release immediately before degradation",
            "host_id": host_id,
            "source": "ci",
            "occurred_at": (timestamp - timedelta(minutes=3)).isoformat(),
        },
    )
    assert change.status_code == 201, change.text

    response = await client.get(f"/api/incidents/{incident.id}/evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident"]["id"] == incident.id
    assert body["alert_count"] == 1
    assert body["metric_count"] == 1
    assert body["change_count"] == 1
    assert [item["kind"] for item in body["timeline"]] == ["change", "alert"]
    assert any("Release immediately before degradation" in finding for finding in body["findings"])


@pytest.mark.asyncio
async def test_incident_evidence_route_is_not_captured_as_incident_id(client: AsyncClient) -> None:
    response = await client.get("/api/incidents/evidence")
    assert response.status_code == 404
