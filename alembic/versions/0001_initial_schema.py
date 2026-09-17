"""Initial persistent telemetry schema.

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "hosts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="production"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hosts_name", "hosts", ["name"], unique=True)

    op.create_table(
        "telemetry_records",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cpu", sa.Float(), nullable=False),
        sa.Column("memory", sa.Float(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("network_mbps", sa.Float(), nullable=False),
        sa.Column("requests_per_second", sa.Float(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
    )
    op.create_index("ix_telemetry_records_host_id", "telemetry_records", ["host_id"])
    op.create_index("ix_telemetry_records_timestamp", "telemetry_records", ["timestamp"])

    op.create_table(
        "alert_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("baseline", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alert_records_host_id", "alert_records", ["host_id"])
    op.create_index("ix_alert_records_metric", "alert_records", ["metric"])
    op.create_index("ix_alert_records_status", "alert_records", ["status"])
    op.create_index("ix_alert_records_timestamp", "alert_records", ["timestamp"])


def downgrade() -> None:
    op.drop_table("alert_records")
    op.drop_table("telemetry_records")
    op.drop_table("hosts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
