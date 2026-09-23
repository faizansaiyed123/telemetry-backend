"""Configuration management using pydantic-settings."""

from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET_KEY = "development-only-secret-key-change-before-production-32"
DEFAULT_BOOTSTRAP_ADMIN_PASSWORD = "change-me-in-production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables with sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Configuration follows the conventional uppercase environment-variable
        # names documented in .env.example (for example, DATABASE_URL and
        # CORS_ALLOWED_ORIGINS). Keep env lookup case-insensitive so the
        # documented deployment contract matches Pydantic Settings behavior.
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Telemetry Backend"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://telemetry:telemetry@localhost:5432/telemetry"
    database_echo: bool = False

    jwt_secret_key: str = DEFAULT_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = DEFAULT_BOOTSTRAP_ADMIN_PASSWORD

    telemetry_rate: int = 10
    max_telemetry_rate: int = 100
    max_history_size: int = 5000
    telemetry_persistence_enabled: bool = True
    telemetry_host_name: str = "synthetic-local"
    telemetry_host_environment: str = "development"
    telemetry_retention_enabled: bool = True
    telemetry_retention_hours: int = 168
    telemetry_retention_cleanup_interval_seconds: int = 3600
    telemetry_retention_batch_size: int = 1000
    telemetry_retention_max_batches_per_run: int = 20

    # Optional PostgreSQL LISTEN/NOTIFY fan-out for multi-worker WebSockets.
    # Disabled by default so the normal single-process deployment has zero
    # additional connection/thread overhead.
    distributed_event_fanout_enabled: bool = False
    distributed_event_channel: str = "telemetry_platform_events"
    distributed_event_queue_size: int = 2000

    anomaly_z_threshold: float = 3.0
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    @field_validator(
        "telemetry_rate",
        "max_telemetry_rate",
        "max_history_size",
        "access_token_expire_minutes",
        "telemetry_retention_hours",
        "telemetry_retention_cleanup_interval_seconds",
        "telemetry_retention_batch_size",
        "telemetry_retention_max_batches_per_run",
        "distributed_event_queue_size",
    )
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

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Reject known development secrets when running in production."""
        if self.app_env.strip().lower() != "production":
            return self

        errors: list[str] = []
        if self.jwt_secret_key == DEFAULT_JWT_SECRET_KEY or len(self.jwt_secret_key) < 32:
            errors.append("JWT_SECRET_KEY must be a production secret of at least 32 characters")
        if self.bootstrap_admin_password == DEFAULT_BOOTSTRAP_ADMIN_PASSWORD or len(self.bootstrap_admin_password) < 12:
            errors.append("BOOTSTRAP_ADMIN_PASSWORD must be changed and at least 12 characters")

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_allowed_origins:
            return []
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    return Settings()
