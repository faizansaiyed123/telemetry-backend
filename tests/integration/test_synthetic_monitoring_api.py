"""Integration tests for synthetic monitoring API."""

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
async def test_synthetic_check_crud_and_security(admin_client: AsyncClient) -> None:
    name = f"api-test-{uuid4()}"
    response = await admin_client.post(
        "/api/synthetic-checks",
        json={
            "name": name,
            "url": "https://example.com/health",
            "interval_seconds": 60,
            "enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    check = response.json()
    check_id = check["id"]
    assert check["name"] == name
    assert check["last_run"] is None

    updated = await admin_client.patch(
        f"/api/synthetic-checks/{check_id}",
        json={"expected_status": 204},
    )
    assert updated.status_code == 200
    assert updated.json()["expected_status"] == 204

    runs = await admin_client.get(f"/api/synthetic-checks/{check_id}/runs")
    assert runs.status_code == 200
    assert runs.json()["runs"] == []

    deleted = await admin_client.delete(f"/api/synthetic-checks/{check_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_synthetic_check_blocks_private_target_by_default(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        "/api/synthetic-checks",
        json={
            "name": f"private-{uuid4()}",
            "url": "http://127.0.0.1:8000/health",
            "enabled": False,
        },
    )
    assert response.status_code == 422
    assert "Private or reserved" in response.json()["detail"]


@pytest.mark.asyncio
async def test_manual_run_uses_monitor_and_returns_run(admin_client: AsyncClient, monkeypatch) -> None:
    name = f"manual-run-{uuid4()}"
    created = await admin_client.post(
        "/api/synthetic-checks",
        json={
            "name": name,
            "url": "https://example.com/health",
            "enabled": False,
        },
    )
    assert created.status_code == 201
    check_id = created.json()["id"]

    from app.models.db import SyntheticCheckRun

    run = SyntheticCheckRun(
        id=123456789,
        check_id=check_id,
        checked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        duration_ms=12.5,
        status_code=200,
        success=True,
        error=None,
        consecutive_failures=0,
    )

    async def fake_run_once(received_id: str):
        assert received_id == check_id
        return run

    monkeypatch.setattr(admin_client._transport.app.state.synthetic_monitor, "run_once", fake_run_once)

    response = await admin_client.post(f"/api/synthetic-checks/{check_id}/run")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["check_id"] == check_id

    deleted = await admin_client.delete(f"/api/synthetic-checks/{check_id}")
    assert deleted.status_code == 204
