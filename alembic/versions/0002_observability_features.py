"""Add production observability features.

Revision ID: 0002_observability_features
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_observability_features"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hosts", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("hosts", sa.Column("agent_version", sa.String(length=64), nullable=True))
    op.create_index("ix_hosts_last_seen_at", "hosts", ["last_seen_at"])

    op.add_column(
        "telemetry_records",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="synthetic"),
    )
    op.add_column("telemetry_records", sa.Column("agent_version", sa.String(length=64), nullable=True))
    op.add_column(
        "telemetry_records",
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telemetry_records_source", "telemetry_records", ["source"])
    op.create_index("ix_telemetry_records_received_at", "telemetry_records", ["received_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_host_id", "api_keys", ["host_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("operator", sa.String(length=4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cooldown_seconds", sa.Float(), nullable=False, server_default="300"),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="WARNING"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alert_rules_name", "alert_rules", ["name"], unique=True)
    op.create_index("ix_alert_rules_metric", "alert_rules", ["metric"])
    op.create_index("ix_alert_rules_enabled", "alert_rules", ["enabled"])

    op.add_column(
        "alert_records",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="anomaly"),
    )
    op.add_column(
        "alert_records",
        sa.Column("rule_id", sa.String(length=36), sa.ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_alert_records_source", "alert_records", ["source"])
    op.create_index("ix_alert_records_rule_id", "alert_records", ["rule_id"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_incidents_host_id", "incidents", ["host_id"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_last_seen_at", "incidents", ["last_seen_at"])

    op.create_table(
        "incident_alerts",
        sa.Column("incident_id", sa.String(length=36), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_id", sa.String(length=36), sa.ForeignKey("alert_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("incident_id", "alert_id"),
    )
    op.create_index("ix_incident_alerts_alert_id", "incident_alerts", ["alert_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="success"),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("incident_alerts")
    op.drop_table("incidents")
    op.drop_index("ix_alert_records_rule_id", table_name="alert_records")
    op.drop_index("ix_alert_records_source", table_name="alert_records")
    op.drop_column("alert_records", "rule_id")
    op.drop_column("alert_records", "source")
    op.drop_index("ix_alert_rules_enabled", table_name="alert_rules")
    op.drop_index("ix_alert_rules_metric", table_name="alert_rules")
    op.drop_index("ix_alert_rules_name", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_host_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_telemetry_records_received_at", table_name="telemetry_records")
    op.drop_index("ix_telemetry_records_source", table_name="telemetry_records")
    op.drop_column("telemetry_records", "received_at")
    op.drop_column("telemetry_records", "agent_version")
    op.drop_column("telemetry_records", "source")
    op.drop_index("ix_hosts_last_seen_at", table_name="hosts")
    op.drop_column("hosts", "agent_version")
    op.drop_column("hosts", "last_seen_at")
