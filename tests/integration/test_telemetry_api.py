"""Integration tests for the telemetry REST API."""

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


@pytest.fixture
async def client_with_events(client):
    """Client with some telemetry events generated."""
    import asyncio
    # Give the background task time to generate events
    await asyncio.sleep(0.5)
    return client


class TestTelemetryAPI:
    async def test_current_telemetry(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/current")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert "event" in data
        event = data["event"]
        assert "sequence" in event
        assert "cpu" in event
        assert "memory" in event
        assert "temperature" in event
        assert "network_mbps" in event
        assert "requests_per_second" in event
        assert "error_rate" in event
        assert "latency_ms" in event
        assert "timestamp" in event

    async def test_current_telemetry_before_generation(self, client):
        """Before any events, current should show available=False."""
        # Reset to clear state
        await client.post("/api/simulation/reset")
        # Pause to stop generation
        await client.post("/api/simulation/pause")
        # Reset again to clear history
        await client.post("/api/simulation/reset")
        response = await client.get("/api/telemetry/current")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False

    async def test_history_default_limit(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/history")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "count" in data
        assert "limit" in data
        assert data["limit"] == 100

    async def test_history_custom_limit(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/history?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] <= 5
        assert data["limit"] == 5

    async def test_history_invalid_limit_zero(self, client):
        response = await client.get("/api/telemetry/history?limit=0")
        assert response.status_code == 422

    async def test_history_invalid_limit_negative(self, client):
        response = await client.get("/api/telemetry/history?limit=-1")
        assert response.status_code == 422

    async def test_history_limit_exceeds_max(self, client):
        response = await client.get("/api/telemetry/history?limit=10000")
        assert response.status_code == 422

    async def test_history_chronological_order(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/history?limit=20")
        data = response.json()
        events = data["events"]
        if len(events) > 1:
            timestamps = [e["timestamp"] for e in events]
            assert timestamps == sorted(timestamps)

    async def test_stats(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/stats")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "cpu" in data
        assert "memory" in data
        assert data["cpu"]["min"] is not None or data["count"] == 0

    async def test_stats_after_reset(self, client):
        await client.post("/api/simulation/pause")
        await client.post("/api/simulation/reset")
        response = await client.get("/api/telemetry/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    async def test_get_alerts_endpoint(self, client):
        response = await client.get("/api/alerts")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "active_count" in data
        assert "total_count" in data
        assert isinstance(data["alerts"], list)

    async def test_get_alerts_active_only(self, client):
        response = await client.get("/api/alerts?active_only=true")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)


    async def test_history_time_range_filter(self, client_with_events):
        response = await client_with_events.get("/api/telemetry/history?limit=10")
        assert response.status_code == 200
        events = response.json()["events"]
        if len(events) < 3:
            pytest.skip("not enough events generated for range assertion")

        start = events[1]["timestamp"]
        end = events[-1]["timestamp"]
        ranged = await client_with_events.get(
            "/api/telemetry/history",
            params={"limit": 10, "start": start, "end": end},
        )
        assert ranged.status_code == 200
        filtered = ranged.json()["events"]
        assert all(start <= item["timestamp"] < end for item in filtered)

    async def test_stats_time_range_matches_history(self, client_with_events):
        history = await client_with_events.get("/api/telemetry/history?limit=20")
        events = history.json()["events"]
        if len(events) < 2:
            pytest.skip("not enough events generated for range assertion")

        start = events[0]["timestamp"]
        end = events[-1]["timestamp"]
        ranged = await client_with_events.get(
            "/api/telemetry/history",
            params={"limit": 20, "start": start, "end": end},
        )
        stats = await client_with_events.get(
            "/api/telemetry/stats",
            params={"start": start, "end": end},
        )
        assert ranged.status_code == 200
        assert stats.status_code == 200
        assert stats.json()["count"] == ranged.json()["count"]

    async def test_time_range_rejects_inverted_bounds(self, client):
        response = await client.get(
            "/api/telemetry/history",
            params={"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        )
        assert response.status_code == 422
