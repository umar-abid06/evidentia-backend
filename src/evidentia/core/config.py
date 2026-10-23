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


class LLMSettings(BaseModel):
    """Model routing. ``fake`` runs fully offline with deterministic adapters."""

    provider: Literal["openai", "fake"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    reasoning_model: str = "gpt-5.4"
    chat_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_version: str = "1"
    reasoning_timeout_s: float = 180.0
    chat_timeout_s: float = 30.0
    retry_delays_s: tuple[float, ...] = (1.0, 2.0, 4.0)
    max_parallel_calls: int = 6


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    broker_url: str | None = None


class WorkerSettings(BaseModel):
    soft_time_limit_s: int = 1500
    hard_time_limit_s: int = 1800


class StorageSettings(BaseModel):
    backend: Literal["s3", "memory"] = "s3"
    bucket: str = "evidentia-documents"
    region: str = "eu-central-1"
    endpoint_url: str | None = None
    public_endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: SecretStr | None = None
    presign_ttl_s: int = 900
    max_upload_mb: int = 50

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


class IngestionSettings(BaseModel):
    max_queue_depth: int = 50
    reading_chunk_chars: int = 18_000
    review_confidence_threshold: float = 0.80


class KnowledgeSettings(BaseModel):
    embed_batch_size: int = 100
    translation_batch_tokens: int = 6000
    translation_parallelism: int = 6
    translation_retries: int = 2


class CardsSettings(BaseModel):
    bin_lookup_url: str | None = None
    bin_lookup_api_key: SecretStr | None = None
    bin_lookup_timeout_s: float = 5.0


class RetrievalSettings(BaseModel):
    similarity_floor: float = 0.45
    relaxed_floor: float = 0.40
    candidates_per_policy: int = 40
    max_candidates: int = 120
    max_chunks_per_policy: int = 5
    max_chunks_total: int = 15
    max_expansion_terms: int = 12


class TravelSettings(BaseModel):
    guide_ttl_days: int = 180
    stale_generation_minutes: int = 15
    recovery_interval_s: int = 600


class SecuritySettings(BaseModel):
    encryption_keys: list[SecretStr] = Field(
        default_factory=list, description="Fernet keys; the first encrypts, all decrypt."
    )


class CrmSettings(BaseModel):
    webhook_url: str | None = None
    api_key: SecretStr | None = None


class ObservabilitySettings(BaseModel):
    metrics_enabled: bool = True
    otlp_endpoint: str | None = Field(default=None, description="e.g. http://otel-collector:4318")
    service_name: str = "evidentia-api"
    trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)


class McpSettings(BaseModel):
    enabled: bool = False
    service_token: SecretStr | None = Field(
        default=None, description="Bearer token agents present to /mcp/."
    )


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
    llm: LLMSettings = Field(default_factory=LLMSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    cards: CardsSettings = Field(default_factory=CardsSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    travel: TravelSettings = Field(default_factory=TravelSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    crm: CrmSettings = Field(default_factory=CrmSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    mcp: McpSettings = Field(default_factory=McpSettings)

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        if self.environment in {"staging", "production"}:
            secret = self.auth.jwt_secret.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("EVIDENTIA_AUTH__JWT_SECRET must be set to 32+ characters")
            if not self.security.encryption_keys:
                raise ValueError("EVIDENTIA_SECURITY__ENCRYPTION_KEYS must hold a Fernet key")
        if self.mcp.enabled:
            token = self.mcp.service_token.get_secret_value() if self.mcp.service_token else ""
            if len(token) < 32:
                raise ValueError("EVIDENTIA_MCP__SERVICE_TOKEN must be 32+ characters")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
