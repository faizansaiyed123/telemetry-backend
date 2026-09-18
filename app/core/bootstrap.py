"""Startup initialization for the first administrator."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.db import User

logger = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    """Create the first administrator after the database has been migrated.

    Bootstrap must never create schema implicitly: schema ownership belongs to
    Alembic. If the database is unavailable or migrations have not been run,
    startup continues and the administrator can be created on the next start.
    """
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return

    try:
        if "users" not in inspect(engine).get_table_names():
            logger.warning("Skipping bootstrap administrator: database schema is not migrated")
            return

        with SessionLocal() as db:
            existing = db.scalar(
                select(User).where(User.email == settings.bootstrap_admin_email.lower())
            )
            if existing:
                return

            db.add(
                User(
                    email=settings.bootstrap_admin_email.lower(),
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role="admin",
                )
            )
            db.commit()
            logger.info("Bootstrap administrator created")
    except SQLAlchemyError:
        logger.exception("Database unavailable; bootstrap administrator was not created")
