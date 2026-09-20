"""Integration tests for public account signup."""

from uuid import uuid4

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


@pytest.mark.asyncio
async def test_signup_creates_viewer_and_returns_session(client: AsyncClient) -> None:
    email = f"signup-{uuid4().hex}@example.com"
    password = "Signup-test-password-123"

    response = await client.post(
        "/api/auth/signup",
        json={"email": f"  {email.upper()}  ", "password": password},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == email
    assert payload["user"]["role"] == "viewer"
    assert payload["user"]["is_active"] is True
    assert payload["access_token"]

    me_response = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer " + payload["access_token"]},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_conflict(client: AsyncClient) -> None:
    email = f"duplicate-{uuid4().hex}@example.com"
    password = "Signup-test-password-123"

    first = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert first.status_code == 201, first.text

    duplicate = await client.post("/api/auth/signup", json={"email": email.upper(), "password": password})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "An account with this email already exists"


@pytest.mark.asyncio
async def test_signup_validates_email_and_password(client: AsyncClient) -> None:
    invalid_email = await client.post(
        "/api/auth/signup",
        json={"email": "not-an-email", "password": "Signup-test-password-123"},
    )
    assert invalid_email.status_code == 422

    short_password = await client.post(
        "/api/auth/signup",
        json={"email": f"short-{uuid4().hex}@example.com", "password": "short"},
    )
    assert short_password.status_code == 422


@pytest.mark.asyncio
async def test_signup_account_can_login_after_creation(client: AsyncClient) -> None:
    email = f"login-{uuid4().hex}@example.com"
    password = "Signup-test-password-123"

    signup = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201

    login = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "viewer"
