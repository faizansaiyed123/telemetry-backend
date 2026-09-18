"""Integration tests for health and readiness APIs."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


class TestHealthAPI:
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_health_response_structure(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "status" in data
        assert "uptime_seconds" in data
        assert "stream_active" in data
        assert "connected_clients" in data
        assert "events_generated" in data

    async def test_health_status_is_healthy(self, client):
        response = await client.get("/health")
        assert response.json()["status"] == "healthy"

    async def test_health_stream_active_on_startup(self, client):
        response = await client.get("/health")
        assert response.json()["stream_active"] is True

    async def test_health_no_secrets_exposed(self, client):
        response = await client.get("/health")
        text = response.text.lower()
        assert "password" not in text
        assert "secret" not in text
        assert "key" not in text

    async def test_ready_returns_200_when_dependencies_are_available(self, client):
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "database_connected": True,
            "telemetry_manager_available": True,
        }
