"""Add telemetry source metadata.

Revision ID: 0004_telemetry_source_metadata
Revises: 0003_ingestion_idempotency
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_telemetry_source_metadata"
down_revision = "0003_ingestion_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telemetry_records",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="synthetic"),
    )
    op.add_column(
        "telemetry_records",
        sa.Column("agent_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "telemetry_records",
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telemetry_records_source", "telemetry_records", ["source"])
    op.create_index("ix_telemetry_records_received_at", "telemetry_records", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_telemetry_records_received_at", table_name="telemetry_records")
    op.drop_index("ix_telemetry_records_source", table_name="telemetry_records")
    op.drop_column("telemetry_records", "received_at")
    op.drop_column("telemetry_records", "agent_version")
    op.drop_column("telemetry_records", "source")
