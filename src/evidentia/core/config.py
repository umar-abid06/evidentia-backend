"""Typed application settings, loaded from environment variables and `.env`.

Nested sections use a double underscore, e.g. ``EVIDENTIA_DATABASE__URL``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://evidentia:evidentia@localhost:5432/evidentia"
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_s: float = 30.0
    echo: bool = False


_DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me"  # noqa: S105 - rejected outside dev


class AuthSettings(BaseModel):
    jwt_secret: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    issuer: str = "evidentia"
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 14


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVIDENTIA_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "evidentia"
    environment: Environment = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=list)

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        if self.environment in {"staging", "production"}:
            secret = self.auth.jwt_secret.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("EVIDENTIA_AUTH__JWT_SECRET must be set to 32+ characters")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
