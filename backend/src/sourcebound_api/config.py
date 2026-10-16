from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOURCEBOUND_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./.local/sourcebound.db"
    storage_root: Path = Path(".local/source_files")
    max_upload_bytes: int = 50 * 1024 * 1024
    parse_mode: str = "background"  # background for local development; celery for deployments
    celery_broker_url: str = "amqp://sourcebound:change-this-local-password@127.0.0.1:5672//"
    organization_id: str = "00000000-0000-0000-0000-000000000001"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_path: Path = Path(".local/qdrant")
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_embedding_model: str = "prithivida/Splade_PP_en_v1"
    reranker_enabled: bool = True
    reranker_model: str = Field(default="Xenova/ms-marco-MiniLM-L-6-v2", min_length=1, max_length=200)
    retrieval_candidate_limit: int = Field(default=32, ge=8, le=100)
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3.5:2b"
    llm_timeout_seconds: float = 90
    auto_create_schema: bool = True
    auth_mode: Literal["disabled", "oidc"] = "disabled"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_organization_claim: str = "organization_id"
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    otel_service_name: str = Field(default="sourcebound-api", min_length=1, max_length=128)
    trace_otlp_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    trace_export_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    ask_rate_limit_per_minute: int = Field(default=20, ge=1, le=10000)
    upload_rate_limit_per_minute: int = Field(default=6, ge=1, le=1000)
    reprocess_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    organization_ask_rate_limit_per_minute: int = Field(default=500, ge=1, le=100000)
    organization_upload_rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    organization_reprocess_rate_limit_per_minute: int = Field(default=100, ge=1, le=10000)
    organization_max_documents: int = Field(default=1000, ge=1, le=1000000)
    organization_max_storage_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1_048_576, le=10 * 1024 * 1024 * 1024 * 1024)
    api_max_in_flight: int = Field(default=200, ge=1, le=10000)
    ask_max_in_flight: int = Field(default=8, ge=1, le=1000)
    ingestion_max_in_flight: int = Field(default=2, ge=1, le=1000)
    capacity_wait_seconds: float = Field(default=1.0, gt=0, le=30)
    rate_limit_hmac_key: SecretStr = SecretStr("local-development-only-change-this-key")
    store_answer_traces: bool = True
    answer_trace_retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("rate_limit_hmac_key")
    @classmethod
    def validate_rate_limit_hmac_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SOURCEBOUND_RATE_LIMIT_HMAC_KEY must contain at least 32 characters.")
        return value

    @model_validator(mode="after")
    def require_deployment_rate_limit_key(self) -> "Settings":
        uses_development_key = (
            self.rate_limit_hmac_key.get_secret_value()
            == "local-development-only-change-this-key"
        )
        if self.auth_mode == "oidc" and uses_development_key:
            raise ValueError("Set a unique SOURCEBOUND_RATE_LIMIT_HMAC_KEY when OIDC is enabled.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
