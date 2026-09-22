"""Integration coverage for the observability platform backend."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app
from app.utils.time import utc_now


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            login = await ac.post(
                "/api/auth/login",
                data={
                    "username": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert login.status_code == 200, login.text
            ac.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
            yield ac


async def create_agent_credentials(client: AsyncClient):
    host_name = f"agent-host-{uuid4().hex}"
    host = await client.post(
        "/api/hosts",
        json={"name": host_name, "environment": "test"},
    )
    assert host.status_code == 201, host.text
    host_data = host.json()

    key = await client.post(
        "/api/api-keys",
        json={"host_id": host_data["id"], "name": "test-agent"},
    )
    assert key.status_code == 201, key.text
    return host_data, key.json()["api_key"]


@pytest.mark.asyncio
async def test_api_key_auth_ingestion_and_revoke(client: AsyncClient):
    host, api_key = await create_agent_credentials(client)

    timestamp = utc_now()
    body = {
        "agent_version": "test-agent",
        "events": [{
            "timestamp": timestamp.isoformat(),
            "sequence": 1,
            "cpu": 20,
            "memory": 40,
            "temperature": 45,
            "network_mbps": 12,
            "requests_per_second": 0,
            "error_rate": 0,
            "latency_ms": 0,
        }],
    }

    unauthorized = await client.post("/api/ingest/telemetry", json=body)
    assert unauthorized.status_code == 401

    accepted = await client.post(
        "/api/ingest/telemetry",
        json=body,
        headers={"X-API-Key": api_key},
    )
    assert accepted.status_code == 202, accepted.text
    result = accepted.json()
    assert result["received"] == 1
    assert result["accepted"] == 1
    assert result["host_id"] == host["id"]

    keys = await client.get("/api/api-keys")
    assert keys.status_code == 200
    assert keys.json()[0]["key_prefix"]
    assert "api_key" not in keys.json()[0]

    key_id = keys.json()[0]["id"]
    revoked = await client.post(f"/api/api-keys/{key_id}/revoke")
    assert revoked.status_code == 200
    rejected = await client.post(
        "/api/ingest/telemetry",
        json=body,
        headers={"X-API-Key": api_key},
    )
    assert rejected.status_code == 401


@pytest.mark.asyncio
async def test_alert_rule_creates_incident_and_audit_history(client: AsyncClient):
    host, _ = await create_agent_credentials(client)

    rule = await client.post(
        "/api/alert-rules",
        json={
            "name": "Critical CPU",
            "host_id": host["id"],
            "metric": "cpu",
            "operator": "gt",
            "threshold": 80,
            "duration_seconds": 0,
            "severity": "CRITICAL",
            "cooldown_seconds": 30,
        },
    )
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    ingest = await client.post(
        "/api/ingest/telemetry",
        headers={"X-API-Key": (await client.get("/api/api-keys")).json()[0].get("api_key", "")},
        json={"events": [{
            "timestamp": (utc_now() + timedelta(seconds=1)).isoformat(),
            "sequence": 10,
            "cpu": 95,
            "memory": 40,
            "temperature": 45,
            "network_mbps": 12,
            "requests_per_second": 0,
            "error_rate": 0,
            "latency_ms": 0,
        }]},
    )
    # The API key is intentionally not returned by list; obtain a fresh
    # credential for the same host so this test stays explicit.
    assert ingest.status_code == 401

    host, api_key = await create_agent_credentials(client)
    update_rule = await client.patch(
        f"/api/alert-rules/{rule_id}",
        json={
            "name": "Critical CPU",
            "host_id": host["id"],
            "metric": "cpu",
            "operator": "gt",
            "threshold": 80,
            "duration_seconds": 0,
            "severity": "CRITICAL",
            "cooldown_seconds": 30,
            "enabled": True,
        },
    )
    assert update_rule.status_code == 200

    event = {
        "timestamp": (utc_now() + timedelta(seconds=2)).isoformat(),
        "sequence": 1,
        "cpu": 95,
        "memory": 40,
        "temperature": 45,
        "network_mbps": 12,
        "requests_per_second": 0,
        "error_rate": 0,
        "latency_ms": 0,
    }
    ingest = await client.post(
        "/api/ingest/telemetry",
        headers={"X-API-Key": api_key},
        json={"agent_version": "test", "events": [event]},
    )
    assert ingest.status_code == 202, ingest.text

    await asyncio.sleep(0.2)
    incidents = await client.get("/api/incidents")
    assert incidents.status_code == 200, incidents.text
    assert incidents.json()
    incident_id = incidents.json()[0]["id"]

    acknowledged = await client.post(f"/api/incidents/{incident_id}/acknowledge")
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"

    resolved = await client.post(f"/api/incidents/{incident_id}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    metrics = await client.get("/api/system/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["events_ingested"] >= 1
    assert metrics.json()["enabled_alert_rules"] >= 1

    audit = await client.get("/api/audit-logs")
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()}
    assert "api_key.created" in actions
    assert "alert_rule.created" in actions
    assert "incident.acknowledged" in actions
    assert "incident.resolved" in actions

    deleted = await client.delete(f"/api/alert-rules/{rule_id}")
    assert deleted.status_code == 204
