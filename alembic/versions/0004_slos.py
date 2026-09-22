"""Add SLO definitions.

Revision ID: 0004_slos
Revises: 0003_ingestion_idempotency
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_slos"
down_revision = "0003_ingestion_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("operator", sa.String(length=4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("objective_percent", sa.Float(), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False, server_default="168"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_slos_name", "slos", ["name"], unique=True)
    op.create_index("ix_slos_host_id", "slos", ["host_id"])
    op.create_index("ix_slos_enabled", "slos", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_slos_enabled", table_name="slos")
    op.drop_index("ix_slos_host_id", table_name="slos")
    op.drop_index("ix_slos_name", table_name="slos")
    op.drop_table("slos")
