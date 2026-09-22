"""Add change events for incident correlation.

Revision ID: 0007_change_events
Revises: 0006_telemetry_storage_hardening
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_change_events"
down_revision = "0006_telemetry_storage_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "change_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_ref", sa.String(length=256), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_change_events_host_id", "change_events", ["host_id"])
    op.create_index("ix_change_events_event_type", "change_events", ["event_type"])
    op.create_index("ix_change_events_occurred_at", "change_events", ["occurred_at"])
    op.create_index("ix_change_events_source", "change_events", ["source"])


def downgrade() -> None:
    op.drop_index("ix_change_events_source", table_name="change_events")
    op.drop_index("ix_change_events_occurred_at", table_name="change_events")
    op.drop_index("ix_change_events_event_type", table_name="change_events")
    op.drop_index("ix_change_events_host_id", table_name="change_events")
    op.drop_table("change_events")
