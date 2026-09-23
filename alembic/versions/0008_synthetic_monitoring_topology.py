"""Add synthetic monitoring and service topology.

Revision ID: 0008_synthetic_monitoring_topology
Revises: 0007_change_events
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_synthetic_monitoring_topology"
down_revision = "0006_telemetry_storage_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="production"),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_services_name", "services", ["name"], unique=True)
    op.create_index("ix_services_environment", "services", ["environment"])

    op.create_table(
        "service_dependencies",
        sa.Column("source_service_id", sa.String(length=36), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_service_id", sa.String(length=36), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship", sa.String(length=32), nullable=False, server_default="depends_on"),
        sa.Column("criticality", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("source_service_id", "target_service_id"),
        sa.CheckConstraint("source_service_id <> target_service_id", name="ck_service_dependency_no_self"),
    )
    op.create_index("ix_service_dependencies_target", "service_dependencies", ["target_service_id"])

    op.create_table(
        "synthetic_checks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("service_id", sa.String(length=36), sa.ForeignKey("services.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False, server_default="GET"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="10"),
        sa.Column("expected_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_synthetic_checks_name", "synthetic_checks", ["name"], unique=True)
    op.create_index("ix_synthetic_checks_service_id", "synthetic_checks", ["service_id"])
    op.create_index("ix_synthetic_checks_enabled", "synthetic_checks", ["enabled"])

    op.create_table(
        "synthetic_check_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("check_id", sa.String(length=36), sa.ForeignKey("synthetic_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_synthetic_check_runs_check_time",
        "synthetic_check_runs",
        ["check_id", "checked_at"],
    )
    op.create_index("ix_synthetic_check_runs_success", "synthetic_check_runs", ["success"])


def downgrade() -> None:
    op.drop_table("synthetic_check_runs")
    op.drop_index("ix_synthetic_checks_enabled", table_name="synthetic_checks")
    op.drop_index("ix_synthetic_checks_service_id", table_name="synthetic_checks")
    op.drop_index("ix_synthetic_checks_name", table_name="synthetic_checks")
    op.drop_table("synthetic_checks")
    op.drop_index("ix_service_dependencies_target", table_name="service_dependencies")
    op.drop_table("service_dependencies")
    op.drop_index("ix_services_environment", table_name="services")
    op.drop_index("ix_services_name", table_name="services")
    op.drop_table("services")
