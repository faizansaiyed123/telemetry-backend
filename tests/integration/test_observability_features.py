"""Integration coverage for production-oriented observability features."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.rate_limit import rate_limiter
from app.main import create_app


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


def event(sequence: int, *, cpu: float = 40.0, host_id: str | None = None, offset: int = 0) -> dict:
    return {
        "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat(),
        "sequence": sequence,
        "cpu": cpu,
        "memory": 50.0,
        "temperature": 48.0,
        "network_mbps": 100.0,
        "requests_per_second": 500.0,
        "error_rate": 0.2,
        "latency_ms": 30.0,
    }


@pytest.mark.asyncio
async def test_api_key_ingestion_is_idempotent_and_host_scoped(client: AsyncClient) -> None:
    host_name = f"agent-{uuid4().hex}"
    created = await client.post(
        "/api/hosts",
        json={"name": host_name, "environment": "test"},
    )
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]

    key_response = await client.post(
        f"/api/api-keys/hosts/{host_id}",
        json={"name": "ci-agent"},
    )
    assert key_response.status_code == 201, key_response.text
    secret = key_response.json()["secret"]

    payload = {"events": [event(1, cpu=42), event(2, cpu=43)], "agent_version": "test-agent/1.0"}

    first = await client.post(
        "/api/ingest/v1/telemetry",
        json=payload,
        headers={"X-Telemetry-Key": secret},
    )
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] == 2
    assert first.json()["host_id"] == host_id

    duplicate = await client.post(
        "/api/ingest/v1/telemetry",
        json=payload,
        headers={"X-Telemetry-Key": secret},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["accepted"] == 0

    history = await client.get(f"/api/telemetry/history?host_id={host_id}&limit=20")
    assert history.status_code == 200, history.text
    assert history.json()["count"] == 2
    assert {item["host_id"] for item in history.json()["events"]} == {host_id}

    listed = await client.get(f"/api/api-keys?host_id={host_id}")
    assert listed.status_code == 200
    assert listed.json()[0]["host_id"] == host_id
    assert "secret" not in listed.json()[0]


@pytest.mark.asyncio
async def test_revoked_api_key_is_rejected(client: AsyncClient) -> None:
    host_name = f"revoke-{uuid4().hex}"
    host_id = (await client.post("/api/hosts", json={"name": host_name, "environment": "test"})).json()["id"]
    created = await client.post(f"/api/api-keys/hosts/{host_id}", json={"name": "revocable"})
    secret = created.json()["secret"]
    key_id = created.json()["id"]

    revoked = await client.post(f"/api/api-keys/{key_id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    response = await client.post(
        "/api/ingest/v1/telemetry",
        json={"events": [event(1)]},
        headers={"X-Telemetry-Key": secret},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_alert_rule_fires_after_condition_and_creates_incident(client: AsyncClient) -> None:
    name = f"High CPU {uuid4().hex}"
    created = await client.post(
        "/api/alert-rules",
        json={
            "name": name,
            "metric": "cpu",
            "operator": ">=",
            "threshold": 80,
            "duration_seconds": 0,
            "cooldown_seconds": 60,
            "severity": "CRITICAL",
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    manager = client._transport.app.state.telemetry_manager
    from app.models.telemetry import TelemetryEvent

    test_event = TelemetryEvent(
        timestamp=datetime.now(timezone.utc),
        sequence=900001,
        cpu=95,
        memory=50,
        temperature=45,
        network_mbps=10,
        requests_per_second=100,
        error_rate=0.1,
        latency_ms=20,
        host_id=manager.persistence_host_id,
        source="api",
    )
    await manager.process_event(test_event, persist=False)

    alerts = await client.get("/api/alerts?active_only=true")
    assert alerts.status_code == 200
    matching = [a for a in alerts.json()["alerts"] if a["rule_id"] == rule_id]
    assert len(matching) == 1
    assert matching[0]["source"] == "rule"

    incidents = await client.get("/api/incidents")
    assert incidents.status_code == 200
    matching_incidents = [i for i in incidents.json() if matching[0]["id"] in i["alert_ids"]]
    assert len(matching_incidents) == 1


@pytest.mark.asyncio
async def test_rule_resolution_closes_incident(client: AsyncClient) -> None:
    created = await client.post(
        "/api/alert-rules",
        json={
            "name": f"CPU recovery {uuid4().hex}",
            "metric": "cpu",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
            "cooldown_seconds": 60,
            "severity": "WARNING",
        },
    )
    rule_id = created.json()["id"]

    manager = client._transport.app.state.telemetry_manager
    from app.models.telemetry import TelemetryEvent

    base = datetime.now(timezone.utc)
    high = TelemetryEvent(
        timestamp=base,
        sequence=900010,
        cpu=90,
        memory=50,
        temperature=45,
        network_mbps=10,
        requests_per_second=100,
        error_rate=0.1,
        latency_ms=20,
        host_id=manager.persistence_host_id,
        source="api",
    )
    low = high.model_copy(update={"sequence": 900011, "timestamp": base + timedelta(seconds=1), "cpu": 20})
    await manager.process_event(high, persist=False)
    await manager.process_event(low, persist=False)

    alerts = await client.get("/api/alerts")
    matching = [a for a in alerts.json()["alerts"] if a["rule_id"] == rule_id]
    assert matching
    assert any(item["resolved"] for item in matching)

    incidents = await client.get("/api/incidents")
    assert incidents.status_code == 200
    related = [i for i in incidents.json() if any(alert["id"] in i["alert_ids"] for alert in matching)]
    assert related
    assert related[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_incident_acknowledgement_is_audited(client: AsyncClient) -> None:
    created = await client.post(
        "/api/alert-rules",
        json={
            "name": f"Ack rule {uuid4().hex}",
            "metric": "cpu",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
            "cooldown_seconds": 60,
        },
    )
    manager = client._transport.app.state.telemetry_manager
    from app.models.telemetry import TelemetryEvent

    await manager.process_event(
        TelemetryEvent(
            timestamp=datetime.now(timezone.utc),
            sequence=900020,
            cpu=95,
            memory=50,
            temperature=45,
            network_mbps=10,
            requests_per_second=100,
            error_rate=0.1,
            latency_ms=20,
            host_id=manager.persistence_host_id,
            source="api",
        ),
        persist=False,
    )

    incidents = await client.get("/api/incidents")
    incident_id = incidents.json()[0]["id"]

    ack = await client.post(f"/api/incidents/{incident_id}/acknowledge")
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"

    audit = await client.get("/api/observability/audit-logs?limit=50")
    assert audit.status_code == 200
    assert any(
        item["action"] == "incident.acknowledged" and item["resource_id"] == incident_id
        for item in audit.json()
    )


@pytest.mark.asyncio
async def test_internal_observability_metrics_are_exposed(client: AsyncClient) -> None:
    metrics = await client.get("/api/observability/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert "counters" in body
    assert "runtime" in body
    assert "websocket_clients" in body["runtime"] or "connected_clients" in body["runtime"]

    prometheus = await client.get("/api/observability/metrics/prometheus")
    assert prometheus.status_code == 200
    assert "telemetry_platform_uptime_seconds" in prometheus.text
    assert "telemetry_platform_telemetry_generated_total" in prometheus.text


@pytest.mark.asyncio
async def test_signup_rate_limit_returns_429(client: AsyncClient) -> None:
    rate_limiter.reset()
    emails = [f"ratelimit-{uuid4().hex}@example.com" for _ in range(6)]
    responses = []
    for email in emails:
        responses.append(
            await client.post(
                "/api/auth/signup",
                json={"email": email, "password": "Rate-limit-password-123"},
            )
        )

    assert [response.status_code for response in responses[:5]] == [201] * 5
    assert responses[5].status_code == 429
    assert "retry_after" in responses[5].json()
    rate_limiter.reset()


@pytest.mark.asyncio
async def test_resolved_incident_history_survives_runtime_rehydration(client: AsyncClient) -> None:
    created = await client.post(
        "/api/alert-rules",
        json={
            "name": f"restart-rule-{uuid4().hex}",
            "metric": "cpu",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
            "cooldown_seconds": 60,
        },
    )
    assert created.status_code == 201
    manager = client._transport.app.state.telemetry_manager

    from app.models.telemetry import TelemetryEvent

    base = datetime.now(timezone.utc)
    high = TelemetryEvent(
        timestamp=base,
        sequence=901000,
        cpu=95,
        memory=50,
        temperature=45,
        network_mbps=10,
        requests_per_second=100,
        error_rate=0.1,
        latency_ms=20,
        host_id=manager.persistence_host_id,
        source="api",
    )
    low = high.model_copy(
        update={"sequence": 901001, "timestamp": base + timedelta(seconds=1), "cpu": 20}
    )
    await manager.process_event(high, persist=False)
    await manager.process_event(low, persist=False)
    await asyncio.sleep(0.25)

    manager.incident_engine._incidents.clear()
    manager.incident_engine._alert_to_incident.clear()
    manager.incident_engine.hydrate()

    incidents = await client.get("/api/incidents")
    assert incidents.status_code == 200
    assert any(item["status"] == "resolved" for item in incidents.json())


@pytest.mark.asyncio
async def test_slo_status_tracks_error_budget_and_database_aggregation(client: AsyncClient) -> None:
    host_name = f"slo-{uuid4().hex}"
    host_response = await client.post(
        "/api/hosts",
        json={"name": host_name, "environment": "test"},
    )
    assert host_response.status_code == 201, host_response.text
    host_id = host_response.json()["id"]

    key_response = await client.post(
        f"/api/api-keys/hosts/{host_id}",
        json={"name": "slo-agent"},
    )
    assert key_response.status_code == 201, key_response.text
    secret = key_response.json()["secret"]

    base = datetime.now(timezone.utc) - timedelta(seconds=3)
    payload = {
        "events": [
            event(1, cpu=10, offset=0),
            event(2, cpu=95, offset=1),
            event(3, cpu=20, offset=2),
        ],
        "agent_version": "slo-agent/1.0",
    }
    ingest = await client.post(
        "/api/ingest/v1/telemetry",
        json=payload,
        headers={"X-Telemetry-Key": secret},
    )
    assert ingest.status_code == 200, ingest.text

    created = await client.post(
        "/api/slos",
        json={
            "name": f"CPU objective {uuid4().hex}",
            "host_id": host_id,
            "metric": "cpu",
            "operator": "<",
            "threshold": 80,
            "objective_percent": 66.67,
            "window_hours": 1,
        },
    )
    assert created.status_code == 201, created.text
    slo_id = created.json()["id"]

    status_response = await client.get(f"/api/slos/{slo_id}/status")
    assert status_response.status_code == 200, status_response.text
    data = status_response.json()
    assert data["total_samples"] == 3
    assert data["good_samples"] == 2
    assert data["bad_samples"] == 1
    assert data["sli_percent"] == round((2 / 3) * 100, 4)
    assert data["compliant"] is True
    assert data["error_budget_remaining_percent"] == 0.0

    updated = await client.patch(
        f"/api/slos/{slo_id}",
        json={"objective_percent": 80},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["objective_percent"] == 80

    now_non_compliant = await client.get(f"/api/slos/{slo_id}/status")
    assert now_non_compliant.status_code == 200
    assert now_non_compliant.json()["compliant"] is False

    deleted = await client.delete(f"/api/slos/{slo_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/api/slos/{slo_id}/status")
    assert missing.status_code == 404
