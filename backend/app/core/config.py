"""Typed, environment-driven application configuration.

Every value is loaded from environment variables (with a local ``.env`` file for
development) rather than being hardcoded. Secrets are declared as ``SecretStr``
so they are masked in repr/serialisation and never rendered into logs.

Supported variables are documented in the repository-root ``.env.example``.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root (backend/app/core/config.py -> repo root is three parents up).
_REPO_ROOT = Path(__file__).resolve().parents[3]

APP_VERSION = "0.1.0"

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Environment(StrEnum):
    """Deployment environment names."""

    LOCAL = "local"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings.

    Field names map to uppercase environment variables (e.g. ``llm_provider``
    reads ``LLM_PROVIDER``). Only configuration fields supported by the project
    documents are defined here; later phases extend this class rather than
    scattering hardcoded values through the codebase.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- Application ------------------------------------------------------
    app_name: str = "Nyaya"
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    log_level: str = "INFO"

    # --- Application database (DECISIONS.md D-028: PostgreSQL) ------------
    database_url: str = "postgresql+asyncpg://nyaya:nyaya@localhost:5432/nyaya"

    # --- Vector store (DECISIONS.md D-010/D-026: Qdrant) ------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_bns_collection: str = "bns_chunks"
    qdrant_user_document_collection: str = "user_document_chunks"

    # --- Queue / rate limiting (DECISIONS.md D-030/D-043: Redis) ----------
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM provider (DECISIONS.md D-032/D-033/D-034) --------------------
    # The provider is selected by configuration; concrete providers are
    # registered in later phases. Ollama is the keyless default path.
    llm_provider: str = Field(min_length=1, default="ollama")
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None

    # --- Embeddings (DECISIONS.md D-011/D-012) -----------------------------
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_batch_size: int = Field(default=32, gt=0)

    # --- Storage (DECISIONS.md D-029) --------------------------------------
    storage_dir: str = "./storage"

    # --- Upload limits (REQUIREMENTS.md D-044/D-045; DECISIONS.md D-043) ---
    max_upload_size_mb: int = Field(default=20, gt=0)
    allowed_upload_types: str = "application/pdf"

    # --- Rate limits (DECISIONS.md D-043: numeric limits are configuration) -
    rate_limit_chat_per_minute: int = Field(default=20, gt=0)
    rate_limit_upload_per_minute: int = Field(default=5, gt=0)

    # --- Retrieval (DECISIONS.md D-015: initial candidate values) ----------
    dense_top_k: int = Field(default=20, gt=0)
    sparse_top_k: int = Field(default=20, gt=0)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Reject unsupported log levels so logging setup cannot fail silently."""
        normalized = value.upper()
        if normalized not in _VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
            raise ValueError(f"log_level must be one of: {allowed}")
        return normalized

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings object (created once, injectable in tests)."""
    return Settings()
