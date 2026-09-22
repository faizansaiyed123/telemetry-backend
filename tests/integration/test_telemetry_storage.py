"""Integration checks for telemetry storage hardening."""

from sqlalchemy import BigInteger, inspect

from app.db.session import engine


def test_telemetry_sequence_is_bigint_and_idempotency_constraint_exists() -> None:
    inspector = inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("telemetry_records")}
    assert isinstance(columns["sequence"]["type"], BigInteger)

    constraints = {
        item["name"]
        for item in inspector.get_unique_constraints("telemetry_records")
    }
    assert "uq_telemetry_host_sequence" in constraints
