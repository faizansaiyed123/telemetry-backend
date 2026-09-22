"""Integration tests for deterministic incident metric evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import create_app
from app.models.alerts import Alert, Severity
from app.models.db import AlertRecord, Host, TelemetryRecord
from app.utils.time import utc_now


@pytest.mark.asyncio
async def test_incident_evidence_reports_metric_regression() -> None:
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
            assert login.status_code == 200, login.text
            client.headers.update(
                {"Authorization": f"Bearer {login.json()['access_token']}"}
            )

            host_id = str(uuid4())
            with SessionLocal() as db:
                host = Host(
                    id=host_id,
                    name=f"evidence-regression-{uuid4().hex[:8]}",
                    environment="test",
                )
                db.add(host)
                db.flush()

                incident_time = utc_now() - timedelta(minutes=2)
                rows = []
                for index in range(5):
                    rows.append(
                        TelemetryRecord(
                            host_id=host_id,
                            timestamp=incident_time - timedelta(minutes=10) + timedelta(seconds=index),
                            sequence=9_200_000_000_000 + index,
                            cpu=20,
                            memory=40,
                            temperature=45,
                            network_mbps=10,
                            requests_per_second=100,
                            error_rate=0.1,
                            latency_ms=100,
                            source="api",
                        )
                    )
                for index in range(5):
                    rows.append(
                        TelemetryRecord(
                            host_id=host_id,
                            timestamp=incident_time + timedelta(seconds=index),
                            sequence=9_200_000_000_010 + index,
                            cpu=20,
                            memory=40,
                            temperature=45,
                            network_mbps=10,
                            requests_per_second=100,
                            error_rate=0.1,
                            latency_ms=250,
                            source="api",
                        )
                    )
                db.add_all(rows)

                alert = Alert(
                    id=f"evidence-regression-alert-{uuid4().hex}",
                    timestamp=incident_time,
                    metric="latency_ms",
                    value=250,
                    baseline=100,
                    severity=Severity.CRITICAL,
                    message="latency regression detected",
                    host_id=host_id,
                    source="api",
                )
                db.add(
                    AlertRecord(
                        id=alert.id,
                        host_id=host_id,
                        metric=alert.metric,
                        value=alert.value,
                        baseline=alert.baseline,
                        severity=alert.severity.value,
                        message=alert.message,
                        status="active",
                        acknowledged=False,
                        source=alert.source,
                        rule_id=None,
                        timestamp=alert.timestamp,
                    )
                )
                db.commit()

            incident = app.state.telemetry_manager.incident_engine.on_alert_created(alert)

            response = await client.get(
                f"/api/incidents/{incident.id}/evidence"
            )
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["metric_findings"]
            assert "latency_ms" in body["metric_findings"][0]
            assert "+150.0%" in body["metric_findings"][0]

            with SessionLocal() as db:
                db.delete(db.get(Host, host_id))
                db.commit()
