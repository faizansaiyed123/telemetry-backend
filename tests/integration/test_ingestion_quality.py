"""Integration tests for agent ingestion data-quality accounting."""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
async def admin_client():
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


@pytest.mark.asyncio
async def test_ingestion_reports_fresh_and_duplicate_samples(admin_client: AsyncClient) -> None:
    hosts = await admin_client.get("/api/hosts")
    assert hosts.status_code == 200, hosts.text
    host_id = hosts.json()[0]["id"]

    created = await admin_client.post(
        f"/api/api-keys/hosts/{host_id}",
        json={"name": "ingestion-quality-test"},
    )
    assert created.status_code == 201, created.text
    key = created.json()["secret"]

    timestamp = datetime.now(timezone.utc).isoformat()
    event = {
        "timestamp": timestamp,
        "sequence": 8_700_000_000_001,
        "cpu": 25,
        "memory": 50,
        "temperature": 45,
        "network_mbps": 20,
        "requests_per_second": 10,
        "error_rate": 0.2,
        "latency_ms": 30,
    }
    headers = {"X-Telemetry-Key": key}

    first = await admin_client.post(
        "/api/ingest/v1/telemetry",
        json={"events": [event]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["received"] == 1
    assert first.json()["accepted"] == 1
    assert first.json()["deduplicated"] == 0

    second = await admin_client.post(
        "/api/ingest/v1/telemetry",
        json={"events": [event, event]},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["received"] == 2
    assert second.json()["accepted"] == 0
    assert second.json()["deduplicated"] == 2
