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
    # Environment value = the bootstrap default AND the restart fallback. A
    # key entered in the /settings admin console wins for the running
    # process (D-090) but is held in memory only — it is never written to
    # disk and does not survive a restart.
    llm_api_key: SecretStr | None = None
    # Per-request HTTP timeout for the LLM provider. Local Ollama on a
    # modest GPU can exceed 120 s for a full grounded generation (large
    # evidence prompt); deployments tune this instead of failing mid-stream.
    llm_timeout_seconds: float = Field(default=300.0, gt=0.0)
    # Sampling temperature passed to providers that support it. Kept low by
    # default: grounded legal answers must stay conservative.
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    # Ollama context window (None = the model's default). Grounded prompts
    # with 10 evidence chunks exceed the ~4k default, and Ollama truncates
    # silently — set this to cover system prompt + evidence + answer.
    llm_num_ctx: int | None = Field(default=8192, gt=0)
    # Max generated tokens per response (None = model default). A small
    # local model occasionally rambles past any useful length on grounded
    # prompts; a cap bounds latency and keeps the answer finite.
    llm_num_predict: int | None = Field(default=768, gt=0)
    # Ask reasoning-capable providers to disable native thinking where they
    # document a switch (OpenAI reasoning_effort=none, Ollama think=false).
    # Opt-in: unknown parameters break strict gateways, so providers without
    # a documented switch never receive one — reasoning isolation is instead
    # enforced by the provider-layer sanitizer (app.llm.sanitize) either way.
    llm_disable_reasoning: bool = False
    # Estimated query cost (F-034/F-035): (tokens / 1000) x rate. Local
    # Ollama generation is free by default; hosted providers set real rates.
    llm_cost_per_1k_input_tokens: float = Field(default=0.0, ge=0.0)
    llm_cost_per_1k_output_tokens: float = Field(default=0.0, ge=0.0)

    # --- Embeddings (DECISIONS.md D-011/D-012) -----------------------------
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_batch_size: int = Field(default=32, gt=0)

    # --- Retrieval (DECISIONS.md D-014/D-015; ARCHITECTURE §11/§15) --------
    # Initial tunable values, not assignment claims; golden-set evaluation
    # may adjust them (D-015).
    retrieval_dense_top_k: int = Field(default=20, gt=0)
    retrieval_sparse_top_k: int = Field(default=20, gt=0)
    retrieval_rrf_k: int = Field(default=60, gt=0)
    # Evidence sufficiency threshold in [0, 1]; 0 disables refusal. Tunable,
    # never a hidden final answer-quality claim (ARCHITECTURE §15).
    retrieval_confidence_threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    # Same contract for session document retrieval (cosine similarity of the
    # top hit). Without it any nonzero-overlap chunk would count as
    # sufficient evidence; 0 disables the gate. Note the scale difference:
    # this is a raw HashingEmbedder cosine, where a genuinely matching
    # chunk scores ~0.08 — NOT the RRF-normalized confidence the statute
    # threshold uses — so the floor sits lower by design.
    document_retrieval_confidence_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    # Dense retrieval backend (D-010/D-092). "auto" uses the Qdrant
    # bns_chunks collection when it is reachable and populated (filled by
    # scripts/ingest.py --qdrant-url) and falls back to the in-process
    # cosine index otherwise; "qdrant" requires Qdrant and fails closed
    # (chat 503) when it is unusable; "in-process" never contacts Qdrant.
    # The sparse BM25 index and the deterministic section lookup always run
    # in-process over the JSONL corpus artifact either way.
    retrieval_dense_backend: str = Field(default="auto", pattern="^(auto|qdrant|in-process)$")

    # --- Languages (multilingual support, DECISIONS.md D-077) -------------
    # Detection backend: "script" (default, zero dependencies) or
    # "fasttext" (statistical, needs fasttext package + lid.176.bin model,
    # ~130 MB, CC BY-SA 4.0; distinguishes hi/mr and bn/as lexically).
    language_detection_backend: str = Field(default="script")
    fasttext_model_path: str | None = None
    # Optional AI4Bharat IndicTrans2 translation backend (MIT license,
    # ~1.2 GB per direction, GPU recommended). None = use the configured
    # local LLM provider for query translation instead. Never a paid API.
    indictrans2_model_dir: str | None = None

    # --- Storage (DECISIONS.md D-029) --------------------------------------
    storage_dir: str = "./storage"

    # --- Statutory forms library (REQUIREMENTS B-*; DECISIONS D-002) -------
    # Exact source PDF and generated library directory. The library is
    # produced out-of-band by scripts/extract_forms.py; the API serves from
    # forms_output_dir and fails closed (503) until a manifest exists.
    forms_source_path: str = str(_REPO_ROOT / "data" / "raw" / "BNS_bare_act_2023.pdf")
    forms_output_dir: str = str(_REPO_ROOT / "data" / "forms")
    forms_page_start: int = Field(default=190, ge=1)
    forms_page_end: int = Field(default=249, ge=1)

    # --- Upload limits (REQUIREMENTS.md D-044/D-045; DECISIONS.md D-043) ---
    max_upload_size_mb: int = Field(default=20, gt=0)
    allowed_upload_types: str = "application/pdf"
    # User-document state backend: "memory" (dev/tests) or "redis" (production
    # worker path, D-030 — API and arq worker share the Redis store/index).
    documents_backend: str = Field(default="memory", pattern="^(memory|redis)$")
    # Sliding TTL (seconds) on session document-index Redis keys (vectors,
    # texts, doc-chunk maps). Default 7 days: user legal documents must not
    # be retained forever in Redis; every upsert refreshes the TTL.
    document_session_ttl_seconds: int = Field(default=604800, gt=0)

    # --- Speech (STT/TTS, DECISIONS.md D-079) -------------------------------
    # Providers are independently configurable and replaceable; defaults are
    # the keyless local AI4Bharat models. Heavy model weights load lazily on
    # first use so API startup never depends on torch being installed.
    speech_stt_provider: str = Field(default="faster-whisper", min_length=1)
    speech_tts_provider: str = Field(default="piper", min_length=1)
    speech_stt_model: str = "small"
    # Piper English voice (D-081); Hindi is pinned to hi_IN-pratham-medium.
    speech_tts_model: str = "en_US-lessac-medium"
    # Directory holding baked/downloaded Piper .onnx voices (empty = storage/piper-voices).
    speech_tts_voices_dir: str = ""
    # Optional cloud (OpenAI-compatible) endpoints — opt-in, never default.
    # Example: https://api.openai.com/v1 (OpenAI), https://api.groq.com/openai/v1
    speech_stt_base_url: str = "https://api.openai.com/v1"
    speech_tts_base_url: str = "https://api.openai.com/v1"
    speech_stt_api_key: SecretStr | None = None
    speech_tts_api_key: SecretStr | None = None
    speech_tts_voice: str = "alloy"
    # auto|cuda|cpu — STT and TTS are configured separately because both
    # models must not sit permanently on a small GPU (6 GB VRAM).
    speech_stt_device: str = Field(default="auto", pattern="^(auto|cuda|cpu)$")
    speech_tts_device: str = Field(default="auto", pattern="^(auto|cuda|cpu)$")
    # IndicConformer adapter codes tried for auto-detection (acoustic
    # scoring picks the winner); comma-separated two-letter codes.
    speech_stt_auto_languages: str = "en,hi,bn,ta"
    # Max accepted audio upload size for transcription.
    max_audio_upload_mb: int = Field(default=15, gt=0)
    # Eagerly load STT/TTS weights at startup (background thread) so the
    # first speech request doesn't pay the model-load latency (~30-60 s on
    # CPU). Off by default: it costs ~3 GB RAM held permanently.
    speech_preload: bool = False

    # --- Admin console (Settings page; DECISIONS.md D-080) ------------------
    # Credentials come from the environment only — never hardcoded, never in
    # the frontend, never returned by any API. When unset the admin API is
    # disabled entirely (login returns 503 ADMIN_DISABLED).
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    # Secret used to sign admin session cookies; derived from the password
    # when unset (documented fallback — set it explicitly in production).
    admin_session_secret: SecretStr | None = None
    # Where the persisted admin configuration lives (JSON, 0600). Console-
    # entered API keys are stored inside it as Fernet CIPHERTEXT (D-098) —
    # never plaintext. Empty disables persistence (settings changes are
    # process-local only).
    admin_settings_path: str = ""
    # Master key for the encrypted console secrets. When unset, a key is
    # generated ONCE and stored as secret.key next to admin_settings_path
    # (same persistent volume) — stable across container recreation. Set it
    # explicitly (urlsafe-base64 32 bytes) to manage rotation yourself; a
    # changed key makes stored secrets undecryptable (they are preserved,
    # never deleted, and the environment values apply instead).
    secrets_master_key: SecretStr | None = None

    # --- Chat history (memory configuration; DECISIONS.md D-080) ------------
    # Conversation memory is client-side (localStorage) and sent per request;
    # history is untrusted input and never a source of legal authority. This
    # caps how many turns the server accepts.
    chat_history_max_turns: int = Field(default=20, ge=1, le=50)

    # --- Rate limits (DECISIONS.md D-043: numeric limits are configuration) -
    rate_limit_chat_per_minute: int = Field(default=20, gt=0)
    rate_limit_upload_per_minute: int = Field(default=5, gt=0)
    rate_limit_speech_per_minute: int = Field(default=10, gt=0)

    # --- Retrieval (DECISIONS.md D-015: initial candidate values) ----------
    dense_top_k: int = Field(default=20, gt=0)
    sparse_top_k: int = Field(default=20, gt=0)
    # Phase 2 chunk artifact serving statute retrieval. Empty disables the
    # chat retrieval seam (503) until a corpus is ingested.
    retrieval_corpus_path: str = ""
    # On-disk cache of the corpus dense vectors so an API restart reloads
    # them instead of re-embedding the corpus (A2-013 one-time embedding).
    # Empty uses "<storage_dir>/retrieval_dense_vectors.json"; "none" disables.
    retrieval_vector_cache_path: str = ""
    # Dense embedding backend (D-011/D-012): "bge" = the open-weight
    # BAAI/bge-base-en-v1.5 model via sentence-transformers (semantic, 768-dim);
    # "hashing" = deterministic bag-of-words fallback (dev/tests, no model).
    embedding_backend: str = Field(default="bge", pattern="^(bge|hashing)$")

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
