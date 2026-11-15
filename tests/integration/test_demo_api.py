"""Integration tests for the isolated, no-signup recruiter workspace."""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from app.core.rate_limit import rate_limiter
from app.main import create_app
from app.models.db import User
from app.db.session import SessionLocal


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_session(client: TestClient) -> dict:
    response = client.post("/api/demo/session")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["is_demo"] is True
    assert body["user"]["demo_expires_at"]
    return body


def test_demo_is_hidden_when_feature_flag_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ENABLED", "false")
    rate_limiter.reset()
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/demo/session")

    assert response.status_code == 404
    assert response.json()["detail"] == "Demo mode is unavailable"
    rate_limiter.reset()


def test_two_demo_sessions_have_independent_telemetry_and_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_MAX_ACTIVE_SESSIONS_PER_IP", "2")
    rate_limiter.reset()
    app = create_app()

    with TestClient(app) as client:
        first = _create_session(client)
        second = _create_session(client)
        first_headers = _headers(first["access_token"])
        second_headers = _headers(second["access_token"])
        first_id = first["user"]["id"]
        second_id = second["user"]["id"]

        registry = app.state.demo_telemetry_registry
        first_manager = registry._sessions[first_id].manager
        second_manager = registry._sessions[second_id].manager

        assert first_manager is not second_manager
        assert first_manager._persistence_enabled is False
        assert first_manager._notifications_enabled is False
        assert first_manager._event_bus is None

        # Each visitor may use their own editor/simulation controls.
        time.sleep(0.25)
        assert first_manager.events_generated > 0
        assert second_manager.events_generated > 0

        paused = client.post("/api/simulation/pause", headers=first_headers)
        assert paused.status_code == 200
        assert client.get("/api/simulation/status", headers=first_headers).json()["running"] is False
        assert client.get("/api/simulation/status", headers=second_headers).json()["running"] is True

        # Resetting one runtime cannot clear the other visitor's history.
        reset = client.post("/api/simulation/reset", headers=first_headers)
        assert reset.status_code == 200
        first_history = client.get("/api/telemetry/history", headers=first_headers)
        second_history = client.get("/api/telemetry/history", headers=second_headers)
        assert first_history.status_code == 200
        assert first_history.json()["count"] == 0
        assert second_history.status_code == 200
        assert second_history.json()["count"] > 0

        # The series endpoint must aggregate the per-session in-memory stream.
        series = client.get("/api/telemetry/series?metric=cpu", headers=second_headers)
        assert series.status_code == 200, series.text
        assert series.json()["host_id"] == "demo"
        assert series.json()["metric"] == "cpu"

        # Explicit host identifiers must never select shared/other telemetry.
        denied_host = client.get(
            "/api/telemetry/history?host_id=production-host",
            headers=first_headers,
        )
        assert denied_host.status_code == 403

        # A third live session from the same edge identity is rate-limited.
        third = client.post("/api/demo/session")
        assert third.status_code == 429

        # WebSocket auth must attach to the caller's runtime, not the global one.
        ws_token = client.post("/api/auth/ws-token", headers=second_headers)
        assert ws_token.status_code == 200, ws_token.text
        with client.websocket_connect(f"/ws/telemetry?token={ws_token.json()['access_token']}") as ws:
            message = ws.receive_json()
            assert message["type"] == "telemetry"
            assert second_manager.connected_clients == 1
            assert app.state.telemetry_manager.connected_clients == 0

        # End each temporary session and verify its access is revoked.
        ended_first = client.post("/api/demo/session/end", headers=first_headers)
        ended_second = client.post("/api/demo/session/end", headers=second_headers)
        assert ended_first.status_code == 200
        assert ended_second.status_code == 200
        assert first_manager.running is False
        assert second_manager.running is False
        assert client.get("/api/simulation/status", headers=first_headers).status_code == 401
        assert client.get("/api/simulation/status", headers=second_headers).status_code == 401

    with SessionLocal() as db:
        assert db.get(User, first["user"]["id"]) is None
        assert db.get(User, second["user"]["id"]) is None
    rate_limiter.reset()


@pytest.mark.parametrize(
    "path",
    [
        "/api/hosts",
        "/api/changes",
        "/api/slos",
        "/api/slos/not-a-real-slo/status",
        "/api/topology",
        "/api/synthetic-checks",
        "/api/synthetic-checks/not-a-real-check/runs",
        "/api/incidents/not-a-real-incident/evidence",
    ],
)
def test_demo_cannot_read_shared_database_resources(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("DEMO_ENABLED", "true")
    rate_limiter.reset()
    app = create_app()

    with TestClient(app) as client:
        session = _create_session(client)
        response = client.get(path, headers=_headers(session["access_token"]))
        assert response.status_code == 403, f"{path}: {response.status_code} {response.text}"
        ended = client.post("/api/demo/session/end", headers=_headers(session["access_token"]))
        assert ended.status_code == 200

    rate_limiter.reset()


def test_demo_identity_cannot_change_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_ENABLED", "true")
    rate_limiter.reset()
    app = create_app()

    with TestClient(app) as client:
        session = _create_session(client)
        response = client.post(
            "/api/auth/change-password",
            json={
                "current_password": "not-a-real-password",
                "new_password": "another-not-a-real-password",
            },
            headers=_headers(session["access_token"]),
        )
        assert response.status_code == 403
        ended = client.post("/api/demo/session/end", headers=_headers(session["access_token"]))
        assert ended.status_code == 200

    rate_limiter.reset()
