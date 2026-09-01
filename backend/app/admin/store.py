"""Admin settings store (Settings page persistence; DECISIONS.md D-080).

Persists the admin-configurable subset of application settings as JSON with
0600 permissions. Precedence (strongest last):

1. Environment / code defaults (``Settings``)
2. Persisted admin configuration (this store)
3. Runtime application configuration

The persisted file is SECRET-FREE by construction: API keys entered in the
console live in memory for the running process only and are never written to
disk (no plaintext secrets at rest). A console key still WINS over an
environment-provided value while that process runs (D-090): the admin console
is the authoritative place to rotate provider keys, and a deployment-time env
key is only the bootstrap default — which is also what applies again after a
restart, because the console key does not survive one. A legacy file that
still contains plaintext secrets is migrated on first load: the secrets are
adopted for the current process and scrubbed from the file immediately.
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

#: Secret settings (API keys). Session-only (in memory), never on disk,
#: masked in reads.
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
        # Console-entered secrets for THIS process only (never persisted —
        # the on-disk file must stay secret-free at all times).
        self._runtime_secrets: dict[str, str] = {}

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, dict[str, Any]]:
        """Return {"settings": ..., "secrets": ..., "corpus": ...}.

        ``secrets`` are the session secrets (console-entered in this process,
        or adopted from a legacy plaintext file during migration) — they are
        never read back from disk. A legacy file that still contains
        plaintext secrets is rewritten without them immediately; the values
        keep working for the current process only.
        """
        # Empty path (persistence disabled) or a directory: no stored state.
        if not self._path.name or not self._path.is_file():
            return {"settings": {}, "secrets": dict(self._runtime_secrets), "corpus": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("admin settings file unreadable; ignoring")
            return {"settings": {}, "secrets": dict(self._runtime_secrets), "corpus": {}}
        settings = {
            key: value
            for key, value in (raw.get("settings") or {}).items()
            if key in EDITABLE_FIELDS
        }
        legacy_secrets = {
            key: value
            for key, value in (raw.get("secrets") or {}).items()
            if key in SECRET_FIELDS and value
        }
        corpus = raw.get("corpus") if isinstance(raw.get("corpus"), dict) else {}
        if legacy_secrets:
            # Migration (no-plaintext-at-rest): adopt the legacy secrets for
            # this process and scrub them from the file on disk right away.
            self._runtime_secrets.update(legacy_secrets)
            self._write(settings, corpus)
            logger.info(
                "removed plaintext secrets from %s; adopted for this process only "
                "(a restart falls back to the environment values)",
                self._path,
            )
        return {"settings": settings, "secrets": dict(self._runtime_secrets), "corpus": corpus}

    def save(
        self,
        settings: dict[str, Any],
        secrets: dict[str, Any],
        corpus: dict[str, Any] | None = None,
    ) -> None:
        """Persist whitelisted non-secret settings (+ corpus manifest) atomically.

        Secrets are session-only: they replace the in-memory store and are
        never written to disk. A store with an empty path skips the disk
        write (persistence disabled); changes then apply in-memory only.
        """
        self._runtime_secrets = {
            key: value for key, value in secrets.items() if key in SECRET_FIELDS and value
        }
        if not str(self._path) or not self._path.name:
            return
        clean_settings = {k: v for k, v in settings.items() if k in EDITABLE_FIELDS}
        self._write(clean_settings, corpus or {})

    def _write(self, settings: dict[str, Any], corpus: dict[str, Any]) -> None:
        """Atomic file write — the payload never contains secrets."""
        payload = {"settings": settings, "secrets": {}, "corpus": corpus}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def apply_overrides(self, base: Settings) -> Settings:
        """Merge persisted values over the environment-provided settings.

        Persisted console settings (non-secret fields) override the
        environment; a session secret overrides an environment-provided key
        (D-090). An activated replacement corpus (``corpus.path``) overrides
        the environment's corpus path until a new corpus is activated.
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
            self._log_effective(base, overridden=[])
            return base
        try:
            effective = Settings(**{**base.model_dump(), **merged})
        except Exception:
            logger.warning("persisted admin settings invalid; using environment settings")
            self._log_effective(base, overridden=[])
            return base
        self._log_effective(effective, overridden=sorted(persisted["settings"]))
        return effective

    def _log_effective(self, settings: Settings, overridden: list[str]) -> None:
        """Startup honesty line: state what actually runs and where it came from.

        The environment (.env / compose) can silently disagree with the
        persisted console configuration — this line makes that drift visible
        instead of leaving a stale LLM_* env triple to be misread as the
        runtime config. Secret VALUES are never logged.
        """
        source = "admin console (persisted)" if overridden else "environment"
        extra = f"; overrides env for: {', '.join(overridden)}" if overridden else ""
        logger.info(
            "effective LLM config: provider=%s model=%s base_url=%s (source: %s%s)",
            settings.llm_provider,
            settings.llm_model,
            settings.llm_base_url,
            source,
            extra,
        )


def mask_secret(value: str | None) -> str:
    """Never echo a secret back; report only whether one is configured."""
    return "set" if value else ""
