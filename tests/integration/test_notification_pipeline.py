"""Integration coverage for notification channels and durable deliveries."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import create_app
from app.models.db import AlertRecord, NotificationChannel, NotificationDelivery
from app.services.notification_dispatcher import NotificationDispatcher, NotificationEnvelope


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
def database():
    with SessionLocal() as db:
        yield db


@pytest.mark.asyncio
async def test_channel_crud_and_masked_secret(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.notification_channels.validate_webhook_url",
        lambda url: None,
    )

    response = await client.post(
        "/api/notification-channels",
        json={
            "name": "Ops webhook",
            "webhook_url": "https://hooks.example.com/telemetry",
            "min_severity": "WARNING",
            "notify_alerts": False,
            "notify_incidents": True,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Ops webhook"
    assert created["webhook_url_masked"] == "https://hooks.example.com/…"
    assert "webhook_url" not in created
    assert created["notify_alerts"] is False

    channel_id = created["id"]
    response = await client.get("/api/notification-channels")
    assert response.status_code == 200
    assert response.json()[0]["id"] == channel_id

    response = await client.patch(
        f"/api/notification-channels/{channel_id}",
        json={"enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    response = await client.get("/api/notification-channels/deliveries")
    assert response.status_code == 200
    assert response.json() == []

    response = await client.delete(f"/api/notification-channels/{channel_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_duplicate_channel_name_returns_conflict(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.notification_channels.validate_webhook_url",
        lambda url: None,
    )
    payload = {
        "name": "Duplicate webhook",
        "webhook_url": "https://hooks.example.com/one",
    }

    first = await client.post("/api/notification-channels", json=payload)
    assert first.status_code == 201

    second = await client.post(
        "/api/notification-channels",
        json={**payload, "webhook_url": "https://hooks.example.com/two"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_dispatcher_materializes_and_delivers_once(database, monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_dispatcher.validate_webhook_url",
        lambda url: None,
    )

    channel = NotificationChannel(
        name="Delivery test channel",
        webhook_url="https://hooks.example.com/telemetry",
        min_severity="WARNING",
        notify_alerts=True,
        notify_incidents=False,
        enabled=True,
    )
    database.add(channel)
    database.flush()

    alert = AlertRecord(
        id="notification-test-alert",
        host_id=None,
        metric="cpu",
        value=95.0,
        baseline=50.0,
        severity="CRITICAL",
        message="CPU threshold exceeded",
        status="active",
        acknowledged=False,
        source="rule",
        rule_id=None,
    )
    database.add(alert)
    database.commit()

    class FakeResponse:
        status_code = 204

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.notification_dispatcher.httpx.AsyncClient",
        FakeAsyncClient,
    )

    dispatcher = NotificationDispatcher(timeout_seconds=1)
    envelope = NotificationEnvelope(
        event_type="alert",
        event_id=alert.id,
        severity=alert.severity,
        alert_id=alert.id,
    )

    await dispatcher._materialize_and_dispatch(envelope)
    await dispatcher._materialize_and_dispatch(envelope)

    rows = list(
        database.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.channel_id == channel.id,
                NotificationDelivery.event_id == alert.id,
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].status == "delivered"
    assert rows[0].attempts == 1
    assert rows[0].delivered_at is not None


@pytest.mark.asyncio
async def test_dispatcher_schedules_retry_after_server_failure(database, monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_dispatcher.validate_webhook_url",
        lambda url: None,
    )

    channel = NotificationChannel(
        name="Retry test channel",
        webhook_url="https://hooks.example.com/retry",
        min_severity="WARNING",
        notify_alerts=True,
        notify_incidents=False,
        enabled=True,
    )
    database.add(channel)
    database.flush()

    alert = AlertRecord(
        id="notification-retry-alert",
        host_id=None,
        metric="latency_ms",
        value=900.0,
        baseline=30.0,
        severity="WARNING",
        message="Latency threshold exceeded",
        status="active",
        acknowledged=False,
        source="rule",
        rule_id=None,
    )
    database.add(alert)
    database.commit()

    class FakeResponse:
        status_code = 503

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.notification_dispatcher.httpx.AsyncClient",
        FakeAsyncClient,
    )

    dispatcher = NotificationDispatcher(timeout_seconds=1)
    await dispatcher._materialize_and_dispatch(
        NotificationEnvelope(
            event_type="alert",
            event_id=alert.id,
            severity=alert.severity,
            alert_id=alert.id,
        )
    )

    row = database.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_id == alert.id)
    )
    assert row is not None
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_attempt_at is not None
    assert row.last_error == "Webhook returned HTTP 503"
