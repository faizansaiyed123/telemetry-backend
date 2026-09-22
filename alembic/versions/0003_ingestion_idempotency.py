"""Add telemetry ingestion idempotency constraint.

Revision ID: 0003_ingestion_idempotency
Revises: 0002_observability_features
"""

from alembic import op

revision = "0003_ingestion_idempotency"
down_revision = "0002_observability_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_telemetry_host_sequence",
        "telemetry_records",
        ["host_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_telemetry_host_sequence", "telemetry_records", type_="unique")
