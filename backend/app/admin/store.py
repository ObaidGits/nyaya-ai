"""Admin settings store (Settings page persistence; DECISIONS.md D-080).

Persists the admin-configurable subset of application settings as JSON with
0600 permissions. Precedence (strongest last):

1. Environment / code defaults (``Settings``)
2. Persisted admin configuration (this store)
3. Runtime application configuration

Secrets (API keys) live in a separate section of the same file — never
returned by GET settings, never logged, always masked in the UI. A secret
saved through the console WINS over an environment-provided value (D-090):
the admin console is the authoritative place to rotate provider keys, and a
deployment-time env key is only the bootstrap default. Secrets stored in the
environment remain masked in every response either way.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Non-secret settings an admin may change at runtime (D-080). Everything
#: else (corpus semantics, security switches, thresholds that implement the
#: assignment's guarantees) is deliberately absent.
EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        # AI / LLM
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "llm_timeout_seconds",
        "llm_temperature",
        "llm_num_predict",
        # Language
        "language_detection_backend",
        # Voice
        "speech_stt_provider",
        "speech_stt_model",
        "speech_stt_device",
        "speech_stt_auto_languages",
        "speech_tts_provider",
        "speech_tts_model",
        "speech_tts_device",
        "speech_tts_voice",
        "speech_tts_voices_dir",
        "speech_preload",
        # Retrieval (operational knobs only — never grounding/citation/refusal)
        "retrieval_dense_top_k",
        "retrieval_sparse_top_k",
        "retrieval_confidence_threshold",
        # Rate limits
        "rate_limit_chat_per_minute",
        "rate_limit_upload_per_minute",
        "rate_limit_speech_per_minute",
        # Memory
        "chat_history_max_turns",
    }
)

#: Secret settings (API keys). Persisted server-side only, masked in reads.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "llm_api_key",
        "speech_stt_api_key",
        "speech_tts_api_key",
    }
)


class AdminSettingsStore:
    """JSON-backed persistence for admin-configurable settings."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, dict[str, Any]]:
        """Return {"settings": ..., "secrets": ..., "corpus": ...}."""
        # Empty path (persistence disabled) or a directory: no stored state.
        if not self._path.name or not self._path.is_file():
            return {"settings": {}, "secrets": {}, "corpus": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("admin settings file unreadable; ignoring")
            return {"settings": {}, "secrets": {}, "corpus": {}}
        settings = {
            key: value
            for key, value in (raw.get("settings") or {}).items()
            if key in EDITABLE_FIELDS
        }
        secrets = {
            key: value for key, value in (raw.get("secrets") or {}).items() if key in SECRET_FIELDS
        }
        corpus = raw.get("corpus") if isinstance(raw.get("corpus"), dict) else {}
        return {"settings": settings, "secrets": secrets, "corpus": corpus}

    def save(
        self,
        settings: dict[str, Any],
        secrets: dict[str, Any],
        corpus: dict[str, Any] | None = None,
    ) -> None:
        """Persist whitelisted settings/secrets/corpus atomically.

        A store with an empty path is a no-op (persistence disabled): changes
        apply in-memory only and do not survive restart.
        """
        if not str(self._path) or not self._path.name:
            return
        clean_settings = {k: v for k, v in settings.items() if k in EDITABLE_FIELDS}
        clean_secrets = {k: v for k, v in secrets.items() if k in SECRET_FIELDS and v}
        payload = {
            "settings": clean_settings,
            "secrets": clean_secrets,
            "corpus": corpus or {},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def apply_overrides(self, base: Settings) -> Settings:
        """Merge persisted values over the environment-provided settings.

        A secret saved through the console overrides an environment-provided
        value (D-090): the console is the authoritative place to rotate
        provider keys, the env value is the bootstrap default. An activated
        replacement corpus (``corpus.path``) overrides the environment's
        corpus path until a new corpus is activated.
        """
        persisted = self.load()
        merged: dict[str, Any] = dict(persisted["settings"])
        for key in SECRET_FIELDS:
            if key in persisted["secrets"]:
                merged[key] = persisted["secrets"][key]
        # Manifest written by corpus activation stores the artifact under
        # "artifact_path"; accept the explicit "path" key too.
        corpus_manifest = persisted.get("corpus") or {}
        corpus_path = corpus_manifest.get("path") or corpus_manifest.get("artifact_path")
        if corpus_path:
            merged["retrieval_corpus_path"] = corpus_path
        if not merged:
            return base
        try:
            return Settings(**{**base.model_dump(), **merged})
        except Exception:
            logger.warning("persisted admin settings invalid; using environment settings")
            return base


def mask_secret(value: str | None) -> str:
    """Never echo a secret back; report only whether one is configured."""
    return "set" if value else ""
