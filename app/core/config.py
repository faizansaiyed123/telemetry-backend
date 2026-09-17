"""Configuration management using pydantic-settings."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables with sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    app_name: str = "Telemetry Backend"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://telemetry:telemetry@localhost:5432/telemetry"
    database_echo: bool = False

    # Authentication
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "change-me-in-production"

    # Telemetry
    telemetry_rate: int = 10
    max_telemetry_rate: int = 100
    max_history_size: int = 5000

    # Anomaly detection
    anomaly_z_threshold: float = 3.0

    # CORS
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    @field_validator("telemetry_rate", "max_telemetry_rate", "max_history_size", "access_token_expire_minutes")
    @classmethod
    def validate_positive_integer(cls, v: int) -> int:
        if v < 1:
            raise ValueError("value must be at least 1")
        return v

    @field_validator("anomaly_z_threshold")
    @classmethod
    def validate_anomaly_z_threshold(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("anomaly_z_threshold must be positive")
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS origins into a list."""
        if not self.cors_allowed_origins:
            return []
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    """Create and return a Settings instance."""
    return Settings()
