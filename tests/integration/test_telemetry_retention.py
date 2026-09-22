"""Integration tests for telemetry retention."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import create_app
from app.models.db import Host, TelemetryRecord
from app.services.telemetry_retention import TelemetryRetentionService


@pytest.mark.asyncio
async def test_retention_deletes_old_rows_and_keeps_recent_rows() -> None:
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

            host_id = str(uuid4())
            old_id = None
            recent_id = None
            now = datetime.now(timezone.utc).replace(microsecond=0)

            with SessionLocal() as db:
                host = Host(id=host_id, name=f"retention-{uuid4().hex[:8]}", environment="test")
                db.add(host)
                db.flush()

                old = TelemetryRecord(
                    host_id=host_id,
                    timestamp=now - timedelta(hours=2),
                    sequence=910000000001,
                    cpu=20,
                    memory=40,
                    temperature=45,
                    network_mbps=10,
                    requests_per_second=20,
                    error_rate=0.1,
                    latency_ms=25,
                    source="agent",
                )
                recent = TelemetryRecord(
                    host_id=host_id,
                    timestamp=now - timedelta(minutes=5),
                    sequence=910000000002,
                    cpu=21,
                    memory=41,
                    temperature=46,
                    network_mbps=11,
                    requests_per_second=21,
                    error_rate=0.2,
                    latency_ms=26,
                    source="agent",
                )
                db.add_all([old, recent])
                db.commit()
                old_id = old.id
                recent_id = recent.id

            service = TelemetryRetentionService(
                retention_hours=1,
                batch_size=1,
                max_batches_per_run=10,
            )
            deleted = await service.run_once(now=now)

            assert deleted == 1

            with SessionLocal() as db:
                assert db.get(TelemetryRecord, old_id) is None
                assert db.get(TelemetryRecord, recent_id) is not None
                db.delete(db.get(Host, host_id))
                db.commit()
