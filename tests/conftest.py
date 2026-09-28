"""Deterministic, isolated test configuration.

Two problems are solved here.

1. Configuration drift. Without this, the suite silently inherits the
   developer's local ``.env``. ``TELEMETRY_SOURCE_MODE=agent`` disables the
   synthetic generator, so telemetry-dependent tests wait for data that never
   arrives: the suite appears to hang and every timing-sensitive test fails.
   Environment variables take precedence over ``.env`` in pydantic-settings,
   so the values below give tests the same deterministic configuration CI gets
   from its defaults, while CI can still override them.

2. Cross-run contamination. The suite used to run against whatever database
   ``DATABASE_URL`` pointed at, including a developer's working database. Tests
   then failed on the second run because rows from the first run were still
   present (unique host names, fixed user emails, deduplicated ingest
   sequences). Tests now always run against a dedicated ``*_test`` database,
   which is created and migrated on demand, so the developer's database is
   never read or written.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

# Tests are not a production deployment: never trigger production secret
# validation in app/core/config.py.
os.environ.setdefault("APP_ENV", "development")

# The suite is written against the hybrid mode used by CI. Individual tests
# that care about agent mode pass source_mode explicitly to TelemetryManager.
os.environ["TELEMETRY_SOURCE_MODE"] = "hybrid"

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-never-for-production-32")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "test-only-password")
# Webhook notifications are enabled by default and require a signing secret of
# at least 32 characters.
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "test-only-webhook-signing-secret-value")


def _dedicated_test_database_url() -> str | None:
    """Point tests at a sibling ``*_test`` database, leaving real data alone."""
    configured = os.environ.get("DATABASE_URL")
    if not configured:
        # Read the developer's .env purely to discover which server to use.
        try:
            from app.core.config import get_settings

            configured = get_settings().database_url
        except Exception:
            return None

    if not configured or not configured.startswith("postgresql"):
        return None

    parts = urlsplit(configured)
    database = (parts.path or "/").lstrip("/")
    if not database or database.endswith("_test"):
        return None

    test_database = f"{database}_test"
    os.environ["DATABASE_URL"] = urlunsplit(
        (parts.scheme, parts.netloc, f"/{test_database}", parts.query, parts.fragment)
    )
    return f"{test_database}|{urlunsplit((parts.scheme, parts.netloc, '/postgres', '', ''))}"


def _prepare_test_database(target: str) -> None:
    """Recreate and migrate the dedicated test database for a clean slate.

    The database name is always derived here and always ends in ``_test``, so
    it is owned exclusively by the suite. Recreating it guarantees tests start
    from the same state as CI regardless of what earlier runs left behind.
    """
    test_database, maintenance_url = target.split("|", 1)
    # psycopg does not understand SQLAlchemy's "+psycopg" driver prefix.
    maintenance_url = maintenance_url.replace("postgresql+psycopg://", "postgresql://", 1)

    if not test_database.endswith("_test"):
        raise RuntimeError(
            f"Refusing to recreate {test_database!r}: test databases must end with '_test'"
        )

    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])

    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        # Terminate stragglers so the drop cannot be blocked by an open session.
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (test_database,),
        )
        connection.execute(f'DROP DATABASE IF EXISTS "{test_database}"')
        connection.execute(f'CREATE DATABASE "{test_database}"')

    from alembic import command
    from alembic.config import Config

    config = Config(str(_repo_root() / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(config, "head")


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


_target = _dedicated_test_database_url()
if _target is not None:
    try:
        _prepare_test_database(_target)
    except Exception as exc:  # pragma: no cover - surfaced as a test error
        raise RuntimeError(
            f"Unable to prepare the isolated test database for {os.environ['DATABASE_URL']}: {exc}"
        ) from exc
