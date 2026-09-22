"""Integration tests for host-scoped telemetry ingestion."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


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


def payload():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "agent_version": "test-agent/1.0",
        "events": [
            {
                "timestamp": now,
                "sequence": 1,
                "cpu": 20,
                "memory": 30,
                "temperature": 45,
                "network_mbps": 10,
                "requests_per_second": 100,
                "error_rate": 0.2,
                "latency_ms": 20,
            },
            {
                "timestamp": now,
                "sequence": 2,
                "cpu": 25,
                "memory": 35,
                "temperature": 46,
                "network_mbps": 12,
                "requests_per_second": 120,
                "error_rate": 0.3,
                "latency_ms": 22,
            },
        ],
    }


@pytest.mark.asyncio
async def test_api_key_is_host_scoped_and_ingestion_is_idempotent(client: AsyncClient) -> None:
    host_name = "agent-test-" + uuid4().hex[:10]
    created = await client.post("/api/hosts", json={"name": host_name, "environment": "test"})
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]

    try:
        key_response = await client.post(
            f"/api/api-keys/hosts/{host_id}",
            json={"name": "integration-agent"},
        )
        assert key_response.status_code == 201, key_response.text
        secret = key_response.json()["secret"]
        listed = await client.get(f"/api/api-keys?host_id={host_id}")
        assert listed.status_code == 200
        assert secret not in listed.text

        body = payload()
        first = await client.post(
            "/api/ingest/v1/telemetry",
            json=body,
            headers={"X-Telemetry-Key": secret},
        )
        assert first.status_code == 200, first.text
        assert first.json()["accepted"] == 2

        duplicate = await client.post(
            "/api/ingest/v1/telemetry",
            json=body,
            headers={"X-Telemetry-Key": secret},
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["accepted"] == 0

        history = await client.get(f"/api/telemetry/history?host_id={host_id}&limit=10")
        assert history.status_code == 200, history.text
        events = history.json()["events"]
        assert [event["sequence"] for event in events] == [1, 2]
        assert all(event["source"] == "agent" for event in events)
        assert all(event["host_id"] == host_id for event in events)
        assert events[-1]["agent_version"] == "test-agent/1.0"

        revoked = await client.post(f"/api/api-keys/{key_response.json()['id']}/revoke")
        assert revoked.status_code == 200, revoked.text

        rejected = await client.post(
            "/api/ingest/v1/telemetry",
            json=payload(),
            headers={"X-Telemetry-Key": secret},
        )
        assert rejected.status_code == 401
    finally:
        await client.delete(f"/api/hosts/{host_id}")
