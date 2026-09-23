"""Add webhook notification channels and delivery history.

Revision ID: 0008_webhook_notifications
Revises: 0007_change_events
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_webhook_notifications"
down_revision = "0007_change_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("event_types", sa.String(length=256), nullable=False, server_default="alert.created,alert.resolved,incident.created,incident.resolved"),
        sa.Column("min_severity", sa.String(length=16), nullable=False, server_default="WARNING"),
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
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
    sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_notification_delivery_event",
        "notification_deliveries",
        ["channel_id", "event_type", "event_id"],
        unique=True,
    )
    op.create_index("ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_index("ix_notification_deliveries_created_at", "notification_deliveries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_created_at", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_channel_id", table_name="notification_deliveries")
    op.drop_index("uq_notification_delivery_event", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_channels_enabled", table_name="notification_channels")
    op.drop_index("ix_notification_channels_name", table_name="notification_channels")
    op.drop_table("notification_channels")
