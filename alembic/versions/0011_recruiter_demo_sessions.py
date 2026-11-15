"""Add disposable recruiter demo identity fields.

Revision ID: 0011_recruiter_demo_sessions
Revises: 0010_service_scoped_incidents
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_recruiter_demo_sessions"
down_revision = "0010_service_scoped_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("demo_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("demo_ip_hash", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_users_is_demo", "users", ["is_demo"])
    op.create_index("ix_users_demo_expires_at", "users", ["demo_expires_at"])
    op.create_index("ix_users_demo_ip_hash", "users", ["demo_ip_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_demo_ip_hash", table_name="users")
    op.drop_index("ix_users_demo_expires_at", table_name="users")
    op.drop_index("ix_users_is_demo", table_name="users")
    op.drop_column("users", "demo_ip_hash")
    op.drop_column("users", "demo_expires_at")
    op.drop_column("users", "is_demo")
