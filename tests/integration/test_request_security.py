"""Integration tests for request correlation and API security headers."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_request_id_is_echoed_and_security_headers_are_present() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            response = await client.get(
                "/health",
                headers={"X-Request-ID": "qa-request-123"},
            )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "qa-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_malformed_request_id_is_replaced() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            response = await client.get(
                "/health",
                headers={"X-Request-ID": "invalid request id with spaces"},
            )

    assert response.status_code == 200
    generated = response.headers["X-Request-ID"]
    assert generated != "invalid request id with spaces"
    assert len(generated) == 32
