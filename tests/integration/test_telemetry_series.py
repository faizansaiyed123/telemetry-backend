"""Integration tests for bounded time-series aggregation."""

from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.db.session import SessionLocal
from app.models.db import Host, TelemetryRecord
from app.utils.time import utc_now


@pytest.fixture
async def client():
    from httpx import ASGITransport
    from app.core.config import get_settings
    from app.main import create_app

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
async def test_series_is_database_aggregated_and_returns_percentile(client: AsyncClient) -> None:
    host_id = str(uuid4())
    with SessionLocal() as db:
        db.add(Host(id=host_id, name=f"series-fixture-{host_id}", environment="test"))
        db.commit()

    end = utc_now() + timedelta(seconds=2)
    start = end - timedelta(minutes=3)
    rows = [
        TelemetryRecord(
            host_id=host_id,
            timestamp=end - timedelta(minutes=2, seconds=20),
            sequence=9_100_000_000_001,
            cpu=20,
            memory=40,
            temperature=45,
            network_mbps=10,
            requests_per_second=100,
            error_rate=0.1,
            latency_ms=10,
            source="api",
        ),
        TelemetryRecord(
            host_id=host_id,
            timestamp=end - timedelta(minutes=2, seconds=10),
            sequence=9_100_000_000_002,
            cpu=30,
            memory=40,
            temperature=45,
            network_mbps=10,
            requests_per_second=100,
            error_rate=0.1,
            latency_ms=20,
            source="api",
        ),
        TelemetryRecord(
            host_id=host_id,
            timestamp=end - timedelta(minutes=2),
            sequence=9_100_000_000_003,
            cpu=40,
            memory=40,
            temperature=45,
            network_mbps=10,
            requests_per_second=100,
            error_rate=0.1,
            latency_ms=30,
            source="api",
        ),
        TelemetryRecord(
            host_id=host_id,
            timestamp=end - timedelta(minutes=1),
            sequence=9_100_000_000_004,
            cpu=50,
            memory=40,
            temperature=45,
            network_mbps=10,
            requests_per_second=100,
            error_rate=0.1,
            latency_ms=80,
            source="api",
        ),
    ]
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    response = await client.get(
        "/api/telemetry/series",
        params={
            "host_id": host_id,
            "metric": "latency_ms",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bucket_seconds": 60,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["host_id"] == host_id
    assert body["metric"] == "latency_ms"
    assert body["bucket_seconds"] == 60
    assert body["points"]
    assert all(point["samples"] >= 1 for point in body["points"])
    assert max(point["p95"] for point in body["points"]) >= 80


@pytest.mark.asyncio
async def test_series_rejects_ranges_that_would_create_too_many_points(client: AsyncClient) -> None:
    end = utc_now()
    start = end - timedelta(hours=24)
    response = await client.get(
        "/api/telemetry/series",
        params={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bucket_seconds": 1,
        },
    )
    assert response.status_code == 422
    assert "5000 points" in response.json()["detail"]


@pytest.mark.asyncio
async def test_series_rejects_invalid_time_order(client: AsyncClient) -> None:
    end = utc_now()
    response = await client.get(
        "/api/telemetry/series",
        params={
            "start": end.isoformat(),
            "end": (end - timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 422
