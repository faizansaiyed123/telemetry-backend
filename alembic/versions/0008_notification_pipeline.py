"""Add durable notification delivery pipeline.

Revision ID: 0008_notification_pipeline
Revises: 0005_telemetry_query_index
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_notification_pipeline"
down_revision = "0007_change_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False, server_default="webhook"),
        sa.Column("webhook_url", sa.String(length=2048), nullable=False),
        sa.Column("min_severity", sa.String(length=16), nullable=False, server_default="WARNING"),
        sa.Column("notify_alerts", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_incidents", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_channels_name", "notification_channels", ["name"], unique=True)
    op.create_index("ix_notification_channels_enabled", "notification_channels", ["enabled"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("channel_id", sa.String(length=36), sa.ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=True),
        sa.Column("alert_id", sa.String(length=36), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_notification_delivery_event",
        "notification_deliveries",
        ["channel_id", "event_type", "event_id"],
    )
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_index("ix_notification_deliveries_next_attempt_at", "notification_deliveries", ["next_attempt_at"])
    op.create_index("ix_notification_deliveries_created_at", "notification_deliveries", ["created_at"])
    op.create_index("ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_channel_id", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_created_at", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_next_attempt_at", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_constraint("uq_notification_delivery_event", "notification_deliveries", type_="unique")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_channels_enabled", table_name="notification_channels")
    op.drop_index("ix_notification_channels_name", table_name="notification_channels")
    op.drop_table("notification_channels")
