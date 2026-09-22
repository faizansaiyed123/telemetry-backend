from app.db.session import Base
from app.models.db import AlertRecord, Host, SLO, TelemetryRecord, User


def test_persistent_models_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "hosts",
        "api_keys",
        "alert_rules",
        "telemetry_records",
        "alert_records",
        "incidents",
        "incident_alerts",
        "audit_logs",
        "slos",
    }


def test_foreign_keys_are_defined() -> None:
    telemetry = TelemetryRecord.__table__
    alerts = AlertRecord.__table__

    assert "hosts.id" in {str(fk.target_fullname) for fk in telemetry.c.host_id.foreign_keys}
    assert "hosts.id" in {str(fk.target_fullname) for fk in alerts.c.host_id.foreign_keys}
