"""Tests for persisted alert lifecycle and acknowledgement API."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app
from app.models.alerts import Alert, Severity
from app.services.alert_persistence import AlertPersistence, AlertPersistenceEvent


def make_alert(alert_id: str = "alert-test") -> Alert:
    return Alert(
        id=alert_id,
        timestamp=datetime.now(timezone.utc),
        metric="cpu",
        value=95.0,
        baseline=45.0,
        severity=Severity.CRITICAL,
        message="CPU anomaly",
    )


@pytest.mark.asyncio
async def test_alert_persistence_creates_and_updates() -> None:
    persistence = AlertPersistence()
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = None

    with patch("app.services.alert_persistence.SessionLocal", return_value=session):
        await persistence._persist(AlertPersistenceEvent(make_alert(), "host-1"))
        assert persistence.persisted_events == 1
        assert session.commit.called
        created = session.add.call_args.args[0]
        assert created.id == "alert-test"
        assert created.host_id == "host-1"

    session.reset_mock()
    session.get.return_value = MagicMock(id="alert-test")
    with patch("app.services.alert_persistence.SessionLocal", return_value=session):
        await persistence._persist(
            AlertPersistenceEvent(make_alert().model_copy(update={"resolved": True}), "host-1")
        )
    assert persistence.persisted_events == 2
    assert session.commit.called
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_alert_acknowledge_endpoint() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            settings = get_settings()
            login = await client.post(
                "/api/auth/login",
                data={
                    "username": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert login.status_code == 200
            client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})

            before = await client.get("/api/alerts")
            assert before.status_code == 200

