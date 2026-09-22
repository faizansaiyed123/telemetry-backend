"""Harden telemetry sequence storage for production agents.

Revision ID: 0006_telemetry_storage_hardening
Revises: 0005_telemetry_query_index
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_telemetry_storage_hardening"
down_revision = "0005_telemetry_query_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "telemetry_records",
        "sequence",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "telemetry_records",
        "sequence",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
