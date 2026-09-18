"""Integration tests for authentication and password management."""

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
            yield ac


async def login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_change_password(client: AsyncClient) -> None:
    ac = client
        settings = get_settings()
        admin_token = await login(ac, settings.bootstrap_admin_email, settings.bootstrap_admin_password)
        ac.headers.update({"Authorization": f"Bearer {admin_token}"})

        email = f"password-test-{uuid4().hex}@example.com"
        old_password = "Old-test-password-123"
        new_password = "New-test-password-456"

        create_response = await ac.post(
            "/api/users",
            json={"email": email, "password": old_password, "role": "viewer"},
        )
        assert create_response.status_code == 201, create_response.text

        user_token = await login(ac, email, old_password)
        ac.headers.update({"Authorization": f"Bearer {user_token}"})

        response = await ac.post(
            "/api/auth/change-password",
            json={"current_password": old_password, "new_password": new_password},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "password_changed"}

        old_login = await ac.post(
            "/api/auth/login",
            data={"username": email, "password": old_password},
        )
        assert old_login.status_code == 401

        new_login = await ac.post(
            "/api/auth/login",
            data={"username": email, "password": new_password},
        )
        assert new_login.status_code == 200
