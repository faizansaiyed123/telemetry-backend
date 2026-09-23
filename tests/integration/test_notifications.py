"""Integration tests for signed webhook notifications and delivery durability."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models.alerts import Alert, Severity
from app.models.db import AuditLog, NotificationDelivery
from app.db.session import SessionLocal
from app.services.notification_dispatcher import NotificationDispatcher, WebhookChannel
from app.main import create_app


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHttpClient:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, *, content: str, headers: dict[str, str]) -> FakeResponse:
        self.calls.append({"url": url, "content": content, "headers": headers})
        return FakeResponse(self.statuses[min(len(self.calls) - 1, len(self.statuses) - 1)])


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            login = await ac.post(
                "/api/auth/login",
                data={
                    "username": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert login.status_code == 200, login.text
            ac.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
            await ac.post("/api/simulation/pause")
            yield ac


@pytest.mark.asyncio
async def test_notification_channel_crud_audits_and_validation(client: AsyncClient) -> None:
    name = f"qa-webhook-{uuid4().hex[:8]}"
    response = await client.post(
        "/api/notification-channels",
        json={
            "name": name,
            "url": "https://example.com/telemetry",
            "event_types": ["alert.created", "incident.created"],
            "min_severity": "WARNING",
        },
    )
    assert response.status_code == 201, response.text
    channel = response.json()
    assert channel["name"] == name
    assert channel["url"] == "https://example.com/telemetry"
    assert channel["event_types"] == ["alert.created", "incident.created"]

    listed = await client.get("/api/notification-channels")
    assert listed.status_code == 200
    assert any(item["id"] == channel["id"] for item in listed.json())

    updated = await client.patch(
        f"/api/notification-channels/{channel['id']}",
        json={"enabled": False, "min_severity": "CRITICAL"},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["min_severity"] == "CRITICAL"

    invalid = await client.post(
        "/api/notification-channels",
        json={"name": f"{name}-bad", "url": "ftp://example.com/hook"},
    )
    assert invalid.status_code == 422

    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "notification_channel",
                AuditLog.resource_id == channel["id"],
                AuditLog.action == "notification_channel.created",
            )
            .order_by(AuditLog.created_at.desc())
        )
        assert audit is not None

    deleted = await client.delete(f"/api/notification-channels/{channel['id']}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_notification_test_endpoint_queues_without_network(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"qa-test-{uuid4().hex[:8]}"
    created = await client.post(
        "/api/notification-channels",
        json={"name": name, "url": "https://example.com/telemetry"},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]

    import app.api.notification_channels as channel_api

    monkeypatch.setattr(
        channel_api.notification_dispatcher,
        "enqueue_test",
        lambda requested_id: "delivery-test-123",
    )
    response = await client.post(f"/api/notification-channels/{channel_id}/test")
    assert response.status_code == 202, response.text
    assert response.json() == {"delivery_id": "delivery-test-123", "status": "queued"}

    await client.delete(f"/api/notification-channels/{channel_id}")


@pytest.mark.asyncio
async def test_signed_delivery_persists_payload_and_target_url() -> None:
    dispatcher = NotificationDispatcher()
    dispatcher._signing_secret = "s" * 32
    fake_client = FakeHttpClient([200])
    dispatcher._client = fake_client

    channel_id = str(uuid4())
    target_url = "https://collector.example.net/v1/alerts"
    dispatcher._channels = {
        channel_id: WebhookChannel(
            id=channel_id,
            name="test",
            url=target_url,
            event_types=frozenset({"alert.created"}),
            min_severity="WARNING",
            enabled=True,
        )
    }

    alert = Alert(
        id=str(uuid4()),
        timestamp=datetime.now(timezone.utc),
        metric="latency_ms",
        value=250,
        baseline=100,
        severity=Severity.CRITICAL,
        message="latency threshold exceeded",
    )
    dispatcher.enqueue_alert(alert, "created")
    job = dispatcher._queue.get_nowait()

    await dispatcher._deliver(job)

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["url"] == target_url
    body = call["content"]
    headers = call["headers"]
    assert headers["X-Telemetry-Timestamp"]
    signed = f'{headers["X-Telemetry-Timestamp"]}.{body}'.encode()
    expected = "sha256=" + hmac.new(
        dispatcher._signing_secret.encode(),
        signed,
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Telemetry-Signature"] == expected
    assert headers["X-Telemetry-Event"] == "alert.created"

    with SessionLocal() as db:
        delivery = db.get(NotificationDelivery, job.delivery_id)
        assert delivery is not None
        assert delivery.status == "delivered"
        assert delivery.attempts == 1
        assert delivery.target_url == target_url
        assert delivery.payload == body
        assert delivery.payload_sha256 == hashlib.sha256(body.encode()).hexdigest()
        db.delete(delivery)
        db.commit()


@pytest.mark.asyncio
async def test_delivery_retries_transient_server_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = NotificationDispatcher()
    fake_client = FakeHttpClient([500, 200])
    dispatcher._client = fake_client

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.notification_dispatcher.asyncio.sleep", no_sleep)

    channel_id = str(uuid4())
    dispatcher._channels = {
        channel_id: WebhookChannel(
            id=channel_id,
            name="retry-test",
            url="https://collector.example.net/retry",
            event_types=frozenset({"alert.created"}),
            min_severity="INFO",
            enabled=True,
        )
    }

    alert = Alert(
        id=str(uuid4()),
        timestamp=datetime.now(timezone.utc),
        metric="cpu",
        value=99,
        baseline=20,
        severity=Severity.WARNING,
        message="cpu high",
    )
    dispatcher.enqueue_alert(alert, "created")
    job = dispatcher._queue.get_nowait()
    await dispatcher._deliver(job)

    assert len(fake_client.calls) == 2

    with SessionLocal() as db:
        delivery = db.get(NotificationDelivery, job.delivery_id)
        assert delivery is not None
        assert delivery.status == "delivered"
        assert delivery.attempts == 2
        db.delete(delivery)
        db.commit()


@pytest.mark.asyncio
async def test_pending_delivery_restores_using_original_target_url() -> None:
    dispatcher = NotificationDispatcher()
    channel_id = str(uuid4())
    channel = WebhookChannel(
        id=channel_id,
        name="restore-test",
        url="https://new.example.net/hook",
        event_types=frozenset({"alert.created"}),
        min_severity="INFO",
        enabled=True,
    )
    dispatcher._channels = {channel_id: channel}

    delivery_id = str(uuid4())
    event_id = str(uuid4())
    payload = {
        "version": 1,
        "event_type": "alert.created",
        "event_id": event_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": {"metric": "cpu"},
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    with SessionLocal() as db:
        db.add(
            NotificationDelivery(
                id=delivery_id,
                channel_id=channel_id,
                target_url="https://old.example.net/hook",
                event_type="alert.created",
                event_id=event_id,
                status="pending",
                attempts=0,
                payload_sha256=hashlib.sha256(body.encode()).hexdigest(),
                payload=body,
            )
        )
        db.commit()

    dispatcher._restore_pending_jobs()
    restored = dispatcher._queue.get_nowait()
    assert restored.delivery_id == delivery_id
    assert restored.url == "https://old.example.net/hook"

    with SessionLocal() as db:
        row = db.get(NotificationDelivery, delivery_id)
        assert row is not None
        db.delete(row)
        db.commit()


@pytest.mark.asyncio
async def test_telemetry_manager_emits_alert_and_incident_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.telemetry import TelemetryEvent
    from app.services.anomaly_detector import AnomalyResult
    from app.services.telemetry_manager import TelemetryManager

    emitted: list[tuple[str, str, str]] = []
    import app.services.telemetry_manager as manager_module

    monkeypatch.setattr(
        manager_module.notification_dispatcher,
        "enqueue_alert",
        lambda alert, action: emitted.append(("alert", action, alert.id)),
    )
    monkeypatch.setattr(
        manager_module.notification_dispatcher,
        "enqueue_incident",
        lambda incident, action: emitted.append(("incident", action, incident.id)),
    )

    manager = TelemetryManager()
    event = TelemetryEvent(
        timestamp=datetime.now(timezone.utc),
        sequence=1,
        cpu=95,
        memory=40,
        temperature=50,
        network_mbps=100,
        requests_per_second=100,
        error_rate=1,
        latency_ms=300,
        host_id="host-test",
        source="agent",
    )

    created = manager._process_anomaly_results(
        [
            AnomalyResult(
                metric="cpu",
                value=95,
                baseline=20,
                z_score=4.2,
                is_anomaly=True,
                severity=Severity.CRITICAL,
            )
        ],
        event,
    )
    assert len(created) == 1
    assert ("alert", "created", created[0].id) in emitted
    assert any(kind == "incident" and action == "created" for kind, action, _ in emitted)

    manager._process_anomaly_results(
        [
            AnomalyResult(
                metric="cpu",
                value=20,
                baseline=20,
                z_score=0,
                is_anomaly=False,
                severity=Severity.INFO,
            )
        ],
        event.model_copy(
            update={"sequence": 2, "timestamp": datetime.now(timezone.utc)}
        ),
    )
    assert any(kind == "alert" and action == "resolved" for kind, action, _ in emitted)
    assert any(kind == "incident" and action == "resolved" for kind, action, _ in emitted)


@pytest.mark.asyncio
async def test_failed_delivery_can_be_requeued(client: AsyncClient) -> None:
    name = f"qa-retry-{uuid4().hex[:8]}"
    created = await client.post(
        "/api/notification-channels",
        json={"name": name, "url": "https://example.com/retry"},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]

    delivery_id = str(uuid4())
    event_id = str(uuid4())
    payload = json.dumps(
        {
            "version": 1,
            "event_type": "alert.created",
            "event_id": event_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": {"metric": "cpu"},
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with SessionLocal() as db:
        db.add(
            NotificationDelivery(
                id=delivery_id,
                channel_id=channel_id,
                target_url="https://example.com/retry",
                event_type="alert.created",
                event_id=event_id,
                status="failed",
                attempts=3,
                last_status_code=503,
                last_error="service unavailable",
                payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                payload=payload,
            )
        )
        db.commit()

    response = await client.post(
        f"/api/notification-channels/deliveries/{delivery_id}/retry"
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"delivery_id": delivery_id, "status": "queued"}

    from app.services.notification_dispatcher import notification_dispatcher
    job = notification_dispatcher._queue.get_nowait()
    assert job.delivery_id == delivery_id
    assert job.url == "https://example.com/retry"

    with SessionLocal() as db:
        row = db.get(NotificationDelivery, delivery_id)
        assert row is not None
        assert row.status == "pending"
        db.delete(row)
        db.delete(db.get(NotificationChannel, channel_id))
        db.commit()


def test_notification_filters_by_event_and_minimum_severity() -> None:
    dispatcher = NotificationDispatcher()
    channel_id = str(uuid4())
    dispatcher._channels = {
        channel_id: WebhookChannel(
            id=channel_id,
            name="critical-only",
            url="https://collector.example.net/critical",
            event_types=frozenset({"alert.created"}),
            min_severity="CRITICAL",
            enabled=True,
        )
    }

    warning_alert = Alert(
        id=str(uuid4()),
        timestamp=datetime.now(timezone.utc),
        metric="cpu",
        value=90,
        baseline=20,
        severity=Severity.WARNING,
        message="cpu warning",
    )
    dispatcher.enqueue_alert(warning_alert, "created")
    assert dispatcher.queue_depth == 0

    critical_alert = warning_alert.model_copy(
        update={"id": str(uuid4()), "severity": Severity.CRITICAL}
    )
    dispatcher.enqueue_alert(critical_alert, "created")
    assert dispatcher.queue_depth == 1

    dispatcher._queue.get_nowait()


def test_production_webhook_destination_rejects_obvious_internal_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.notification_dispatcher as dispatcher_module
    from types import SimpleNamespace

    monkeypatch.setattr(
        dispatcher_module,
        "get_settings",
        lambda: SimpleNamespace(app_env="production"),
    )

    with pytest.raises(ValueError, match="HTTPS"):
        NotificationDispatcher.validate_url("http://example.com/hook")

    with pytest.raises(ValueError, match="Local or metadata"):
        NotificationDispatcher.validate_url("https://localhost/hook")

    with pytest.raises(ValueError, match="Private or loopback"):
        NotificationDispatcher.validate_url("https://127.0.0.1/hook")


@pytest.mark.asyncio
async def test_viewer_cannot_manage_notification_channels() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            signup = await client.post(
                "/api/auth/signup",
                json={
                    "email": f"viewer-{uuid4().hex[:8]}@example.com",
                    "password": "strong-pass-123",
                },
            )
            assert signup.status_code == 201, signup.text
            client.headers.update(
                {"Authorization": f"Bearer {signup.json()['access_token']}"}
            )
            listing = await client.get("/api/notification-channels")
            assert listing.status_code == 403
