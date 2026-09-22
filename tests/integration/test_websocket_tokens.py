"""Integration tests for WebSocket handoff tokens."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


def test_ws_token_requires_authentication(app):
    with TestClient(app) as client:
        response = client.post("/api/auth/ws-token")
        assert response.status_code == 401


def test_ws_token_is_single_use(app):
    with TestClient(app) as client:
        settings = get_settings()
        login = client.post(
            "/api/auth/login",
            data={
                "username": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        assert login.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})

        token_response = client.post("/api/auth/ws-token")
        assert token_response.status_code == 200
        token = token_response.json()["access_token"]

        with client.websocket_connect(f"/ws/telemetry?token={token}") as ws:
            first = ws.receive_json()
            assert first["type"] == "telemetry"

        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect(f"/ws/telemetry?token={token}")
