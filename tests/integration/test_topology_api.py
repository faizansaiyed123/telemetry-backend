"""Integration tests for service registry and topology."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
async def admin_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            response = await client.post(
                "/api/auth/login",
                data={
                    "username": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert response.status_code == 200, response.text
            client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
            yield client


@pytest.mark.asyncio
async def test_service_dependencies_and_topology(admin_client: AsyncClient) -> None:
    suffix = uuid4().hex
    first = await admin_client.post(
        "/api/services",
        json={"name": f"api-{suffix}", "environment": "test"},
    )
    second = await admin_client.post(
        "/api/services",
        json={"name": f"db-{suffix}", "environment": "test"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    source_id = first.json()["id"]
    target_id = second.json()["id"]

    dependency = await admin_client.post(
        f"/api/services/{source_id}/dependencies",
        json={"target_service_id": target_id, "criticality": "critical"},
    )
    assert dependency.status_code == 201
    assert dependency.json()["target_service_id"] == target_id

    duplicate = await admin_client.post(
        f"/api/services/{source_id}/dependencies",
        json={"target_service_id": target_id},
    )
    assert duplicate.status_code == 409

    cycle = await admin_client.post(
        f"/api/services/{target_id}/dependencies",
        json={"target_service_id": source_id},
    )
    assert cycle.status_code == 422
    assert "cycle" in cycle.json()["detail"].lower()

    topology = await admin_client.get("/api/topology")
    assert topology.status_code == 200
    body = topology.json()
    node_ids = {node["id"] for node in body["nodes"]}
    assert source_id in node_ids
    assert target_id in node_ids
    assert any(
        edge["source"] == source_id
        and edge["target"] == target_id
        and edge["criticality"] == "critical"
        for edge in body["edges"]
    )

    delete_dependency = await admin_client.delete(
        f"/api/services/{source_id}/dependencies/{target_id}"
    )
    assert delete_dependency.status_code == 204

    assert (await admin_client.delete(f"/api/services/{source_id}")).status_code == 204
    assert (await admin_client.delete(f"/api/services/{target_id}")).status_code == 204


@pytest.mark.asyncio
async def test_service_duplicate_is_rejected(admin_client: AsyncClient) -> None:
    name = f"duplicate-{uuid4()}"
    first = await admin_client.post(
        "/api/services",
        json={"name": name},
    )
    assert first.status_code == 201

    second = await admin_client.post(
        "/api/services",
        json={"name": name},
    )
    assert second.status_code == 409

    assert (await admin_client.delete(f"/api/services/{first.json()['id']}")).status_code == 204
