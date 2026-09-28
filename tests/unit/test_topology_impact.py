"""Unit tests for deterministic topology impact analysis."""

from __future__ import annotations

from uuid import uuid4

from app.db.session import SessionLocal
from app.models.db import Service, ServiceDependency
from app.services.incident_evidence import build_service_impacts


def test_build_service_impacts_follows_downstream_dependencies_with_hops() -> None:
    suffix = uuid4().hex
    service_a = Service(id=str(uuid4()), name=f"impact-a-{suffix}", environment="test")
    service_b = Service(id=str(uuid4()), name=f"impact-b-{suffix}", environment="test")
    service_c = Service(id=str(uuid4()), name=f"impact-c-{suffix}", environment="test")
    with SessionLocal() as db:
        db.add_all([service_a, service_b, service_c])
        db.flush()
        db.add_all(
            [
                ServiceDependency(
                    source_service_id=service_b.id,
                    target_service_id=service_a.id,
                    relationship="depends_on",
                    criticality="critical",
                ),
                ServiceDependency(
                    source_service_id=service_c.id,
                    target_service_id=service_b.id,
                    relationship="depends_on",
                    criticality="normal",
                ),
            ]
        )
        db.commit()

        impacts = build_service_impacts(db, service_id=service_a.id)
        by_id = {item["service_id"]: item for item in impacts}

        assert by_id[service_b.id]["hops"] == 1
        assert by_id[service_b.id]["critical_dependency"] is True
        assert by_id[service_c.id]["hops"] == 2
        assert by_id[service_c.id]["critical_dependency"] is True

        db.delete(service_c)
        db.delete(service_b)
        db.delete(service_a)
        db.commit()
