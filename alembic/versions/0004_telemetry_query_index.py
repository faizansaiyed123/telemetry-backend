"""Add composite telemetry query index.

Revision ID: 0004_telemetry_query_index
Revises: 0003_ingestion_idempotency
"""

from alembic import op

revision = "0004_telemetry_query_index"
down_revision = "0003_slos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_telemetry_records_host_timestamp",
        "telemetry_records",
        ["host_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_records_host_timestamp", table_name="telemetry_records")
