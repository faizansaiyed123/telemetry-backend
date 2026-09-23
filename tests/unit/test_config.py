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
        webhook_signing_secret="a" * 48,
    )
    assert settings.app_env == "production"


def test_uppercase_environment_variables_match_documented_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("cors_allowed_origins", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == "http://127.0.0.1:3000,http://localhost:3000"
    assert settings.cors_origins_list == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]
