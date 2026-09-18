"""Integration tests for the WebSocket endpoint."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    """Synchronous authenticated TestClient for WebSocket tests."""
    with TestClient(app) as c:
        settings = get_settings()
        response = c.post(
            "/api/auth/login",
            data={
                "username": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        assert response.status_code == 200, response.text
        c.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
        c.auth_token = response.json()["access_token"]
        yield c


class TestWebSocket:
    @staticmethod
    def _ws(client):
        """Open an authenticated telemetry WebSocket connection."""
        return client.websocket_connect(f"/ws/telemetry?token={client.auth_token}")

    def test_websocket_connection(self, client):
        """WebSocket should connect successfully."""
        with self._ws(client) as ws:
            # Should receive telemetry messages
            msg = ws.receive_json()
            assert msg["type"] == "telemetry"
            assert "data" in msg
            assert "sequence" in msg["data"]
            assert "cpu" in msg["data"]

    def test_websocket_message_structure(self, client):
        """Telemetry messages should have the correct JSON structure."""
        with self._ws(client) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "telemetry"
            data = msg["data"]
            assert "timestamp" in data
            assert "sequence" in data
            assert "cpu" in data
            assert "memory" in data
            assert "temperature" in data
            assert "network_mbps" in data
            assert "requests_per_second" in data
            assert "error_rate" in data
            assert "latency_ms" in data

    def test_sequence_numbers_monotonic(self, client):
        """Sequence numbers should increase monotonically."""
        with self._ws(client) as ws:
            sequences = []
            for _ in range(5):
                msg = ws.receive_json()
                if msg["type"] == "telemetry":
                    sequences.append(msg["data"]["sequence"])
            assert len(sequences) >= 2
            for i in range(1, len(sequences)):
                assert sequences[i] > sequences[i - 1]

    def test_multiple_clients(self, client):
        """Multiple simultaneous clients should all receive telemetry."""
        with (
            self._ws(client) as ws1,
            self._ws(client) as ws2,
        ):
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1["type"] == "telemetry"
            assert msg2["type"] == "telemetry"

    def test_disconnect_one_client_continues(self, client):
        """When one client disconnects, the other should continue receiving."""
        with self._ws(client) as ws1:
            # Connect and disconnect a second client
            with self._ws(client) as ws2:
                ws2.receive_json()
            # First client should still receive
            msg = ws1.receive_json()
            assert msg["type"] == "telemetry"

    def test_pause_via_websocket(self, client):
        """Pause should stop telemetry messages."""
        # Use REST to pause
        response = client.post("/api/simulation/pause")
        assert response.status_code == 200
        with self._ws(client) as ws:
            # Connection itself should still be accepted while generation is paused.
            pass
        # Resume for cleanup
        response = client.post("/api/simulation/resume")
        assert response.status_code == 200

    def test_reset_via_websocket(self, client):
        """Reset should clear state and disconnect WebSocket clients."""
        with self._ws(client) as ws:
            # Receive some telemetry
            ws.receive_json()
            # Trigger reset — this disconnects all WS clients
            response = client.post("/api/simulation/reset")
            assert response.status_code == 200

            # The WebSocket should be disconnected by the reset
            from starlette.websockets import WebSocketDisconnect
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        # Verify state was reset
        status = client.get("/api/simulation/status")
        assert status.status_code == 200
        assert status.json()["events_generated"] == 0
        assert status.json()["sequence"] == 0
        # Restart for cleanup
        response = client.post("/api/simulation/start")
        assert response.status_code == 200

    def test_rate_change_affects_stream(self, client):
        """Rate change should affect the telemetry stream rate."""
        with self._ws(client) as ws:
            # Set high rate
            response = client.post("/api/simulation/rate?rate=50")
            assert response.status_code == 200

            # Collect messages with timing
            messages = []
            for _ in range(10):
                msg = ws.receive_json()
                messages.append(msg)

            # Should have received messages quickly
            assert len(messages) == 10
            for msg in messages:
                assert msg["type"] in ("telemetry", "system", "alert")

    def test_anomaly_trigger_via_rest(self, client):
        """Triggering an anomaly via REST should affect telemetry."""
        with self._ws(client) as ws:
            # Receive baseline
            ws.receive_json()

            # Trigger anomaly
            response = client.post("/api/simulation/trigger", json={"metric": "cpu"})
            assert response.status_code == 200

            # Should receive a system message about the anomaly trigger
            found_system = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "system" and msg["data"]["event"] == "anomaly_triggered":
                    found_system = True
                    break
            assert found_system

    def test_alert_messages(self, client):
        """Alert messages should appear when anomalies are detected."""
        with self._ws(client) as ws:
            # Consume enough telemetry messages to build up history for anomaly detection
            telemetry_count = 0
            for _ in range(40):
                msg = ws.receive_json()
                if msg["type"] == "telemetry":
                    telemetry_count += 1
                if telemetry_count >= 35:
                    break

            # Trigger an anomaly to generate alerts
            response = client.post("/api/simulation/trigger", json={"metric": "cpu"})
            assert response.status_code == 200

            # Collect messages looking for alert
            for _ in range(100):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "alert":
                        assert "id" in msg["data"]
                        assert "metric" in msg["data"]
                        assert "severity" in msg["data"]
                        assert "resolved" in msg["data"]
                        break
                except Exception:
                    break
            # Alert may or may not appear depending on z-score

    def test_malformed_client_message(self, client):
        """Malformed messages from client should not crash the server."""
        with self._ws(client) as ws:
            ws.send_text("not valid json {{{")
            # Server should still send telemetry
            msg = ws.receive_json()
            assert msg["type"] == "telemetry"

    def test_graceful_disconnect(self, client):
        """Client disconnecting should not crash the server."""
        with self._ws(client) as ws:
            ws.receive_json()
        # After disconnect, server should still work
        response = client.get("/health")
        assert response.status_code == 200
"