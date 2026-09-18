"""Configuration validation tests."""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_BOOTSTRAP_ADMIN_PASSWORD, DEFAULT_JWT_SECRET_KEY, Settings


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(
            app_env="production",
            jwt_secret_key=DEFAULT_JWT_SECRET_KEY,
            bootstrap_admin_password="a-production-admin-password",
        )


def test_production_rejects_default_bootstrap_password() -> None:
    with pytest.raises(ValidationError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(
            app_env="production",
            jwt_secret_key="a" * 48,
            bootstrap_admin_password=DEFAULT_BOOTSTRAP_ADMIN_PASSWORD,
        )


def test_production_accepts_replaced_secrets() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret_key="a" * 48,
        bootstrap_admin_password="a-safe-production-password",
    )
    assert settings.app_env == "production"
