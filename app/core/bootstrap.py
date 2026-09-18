"""Startup initialization for required seed data."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.db import Host, User

logger = logging.getLogger(__name__)


def bootstrap_data() -> None:
    """Seed the bootstrap administrator and synthetic telemetry host.

    This function never creates schema implicitly; Alembic owns schema creation.
    """
    settings = get_settings()
    try:
        tables = inspect(engine).get_table_names()
        if "users" not in tables or "hosts" not in tables:
            logger.warning("Skipping bootstrap data: database schema is not migrated")
            return

        with SessionLocal() as db:
            if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
                email = settings.bootstrap_admin_email.lower()
                existing_user = db.scalar(select(User).where(User.email == email))
                if existing_user is None:
                    db.add(
                        User(
                            email=email,
                            password_hash=hash_password(settings.bootstrap_admin_password),
                            role="admin",
                        )
                    )
                    logger.info("Bootstrap administrator created")

            if settings.telemetry_persistence_enabled:
                host = db.scalar(select(Host).where(Host.name == settings.telemetry_host_name))
                if host is None:
                    db.add(
                        Host(
                            name=settings.telemetry_host_name,
                            environment=settings.telemetry_host_environment,
                            is_active=True,
                        )
                    )
                    logger.info("Synthetic telemetry host created: %s", settings.telemetry_host_name)

            db.commit()
    except SQLAlchemyError:
        logger.exception("Database unavailable; bootstrap data was not initialized")
