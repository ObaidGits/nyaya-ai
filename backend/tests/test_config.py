"""Configuration tests (REQUIREMENTS.md D-003, SEC-005/SEC-007)."""

import pytest
from app.core.config import Environment, Settings, get_settings
from pydantic import ValidationError


def test_defaults_load() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "Nyaya"
    assert settings.environment == Environment.LOCAL
    assert settings.llm_provider == "ollama"
    assert settings.qdrant_bns_collection == "bns_chunks"
    assert settings.qdrant_user_document_collection == "user_document_chunks"
    assert settings.embedding_model == "BAAI/bge-base-en-v1.5"
    assert settings.dense_top_k == 20
    assert settings.sparse_top_k == 20


def test_configuration_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("DENSE_TOP_K", "7")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "groq"
    assert settings.dense_top_k == 7
    assert settings.log_level == "DEBUG"  # normalised to upper case


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "NOISY")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_numeric_limit_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_empty_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="")


def test_api_key_is_masked_in_serialisation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")
    settings = Settings(_env_file=None)
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "super-secret-value"
    dumped = settings.model_dump_json()
    assert "super-secret-value" not in dumped
    assert "super-secret-value" not in repr(settings)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
