"""Scope alert and incident correlation by service.

Revision ID: 0009_service_scoped_incidents
Revises: 0008_synthetic_monitoring_topology
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_service_scoped_incidents"
down_revision = "0007_synthetic_monitoring_topology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_records",
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_alert_records_service_id", "alert_records", ["service_id"])

    op.add_column(
        "incidents",
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_incidents_service_id", "incidents", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_service_id", table_name="incidents")
    op.drop_column("incidents", "service_id")
    op.drop_index("ix_alert_records_service_id", table_name="alert_records")
    op.drop_column("alert_records", "service_id")
