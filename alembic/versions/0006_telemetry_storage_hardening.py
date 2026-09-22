"""Harden telemetry storage for production agents.

Revision ID: 0006_telemetry_storage_hardening
Revises: 0005_telemetry_query_index
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0006_telemetry_storage_hardening"
down_revision = "0005_telemetry_query_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(
        text(
            """
            SELECT 1
            FROM telemetry_records
            GROUP BY host_id, sequence
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicates is not None:
        raise RuntimeError(
            "Cannot create uq_telemetry_host_sequence because duplicate "
            "(host_id, sequence) rows already exist; clean duplicates explicitly "
            "before running migration 0006."
        )

    op.alter_column(
        "telemetry_records",
        "sequence",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        "uq_telemetry_host_sequence",
        "telemetry_records",
        ["host_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_telemetry_host_sequence",
        "telemetry_records",
        type_="unique",
    )
    op.alter_column(
        "telemetry_records",
        "sequence",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
