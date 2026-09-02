"""Admin settings store (Settings page persistence; DECISIONS.md D-080/D-098).

Persists the admin-configurable subset of application settings as JSON with
0600 permissions. Precedence (strongest last):

1. Environment / code defaults (``Settings``)
2. Persisted admin configuration (this store)
3. Runtime application configuration

Secrets (API keys) are persisted ENCRYPTED AT REST (D-098): Fernet
ciphertext inside the same JSON file, keyed by a master key that is either
operator-provided (``NYAYA_SECRET_KEY``) or generated once and stored as
``secret.key`` next to the file — both live on the same persistent volume,
so console-entered keys survive ``docker compose down``/``up`` and container
recreation. The file never contains PLAINTEXT secrets. A legacy file with
plaintext secrets is migrated on first load: the values are adopted,
re-encrypted, and scrubbed from the file immediately.

If the master key is missing/changed (operator rotation, deleted key file),
stored ciphertext can no longer be decrypted: the stored data is PRESERVED
(not deleted, not overwritten), the affected fields are reported as
unreadable, and the process falls back to the environment values. A console
key still WINS over an environment-provided value (D-090).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from app.admin.secretbox import KEY_FILE_NAME, SecretBox
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

#: Secret settings (API keys). Persisted as Fernet ciphertext inside the
#: admin settings file (D-098), masked in reads — never echoed or logged.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "llm_api_key",
        "speech_stt_api_key",
        "speech_tts_api_key",
    }
)


class AdminSettingsStore:
    """JSON-backed persistence for admin-configurable settings."""

    def __init__(self, path: str, *, secret_env_key: str | None = None) -> None:
        self._path = Path(path) if path else Path("")
        # Console-entered secrets. Loaded from (and saved back to) the
        # encrypted section of the settings file; kept in memory only when
        # encryption is unavailable (persistence disabled).
        self._runtime_secrets: dict[str, str] = {}
        # Fields whose stored ciphertext could not be decrypted with the
        # current master key. The stored data is preserved untouched.
        self.secrets_unreadable: list[str] = []
        # Master key: operator env value wins; otherwise a once-generated
        # key file NEXT TO the settings file (same persistent volume).
        key_path = self._path.parent / KEY_FILE_NAME if self._path.name else None
        self._box = SecretBox(secret_env_key, key_path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def secrets_persisted(self) -> bool:
        """True when console secrets survive process restarts (D-098)."""
        return bool(self._path.name) and self._box.available

    def load(self) -> dict[str, Any]:
        """Return {"settings", "secrets", "corpus", "secrets_unreadable"}.

        ``secrets`` are decrypted from the encrypted section of the file
        (plus any values adopted from a legacy plaintext file). A file whose
        ciphertext no longer matches the master key is NEVER modified: the
        affected fields are reported in ``secrets_unreadable`` and the
        environment values apply instead.
        """
        empty: dict[str, Any] = {
            "settings": {},
            "secrets": dict(self._runtime_secrets),
            "corpus": {},
            "secrets_unreadable": [],
        }
        # Empty path (persistence disabled) or a directory: no stored state.
        if not self._path.name or not self._path.is_file():
            return empty
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("admin settings file unreadable; ignoring")
            return empty
        settings = {
            key: value
            for key, value in (raw.get("settings") or {}).items()
            if key in EDITABLE_FIELDS
        }
        corpus = raw.get("corpus") if isinstance(raw.get("corpus"), dict) else {}
        stored = self._decrypt_stored(raw.get("secrets_encrypted"))
        legacy_secrets = {
            key: value
            for key, value in (raw.get("secrets") or {}).items()
            if key in SECRET_FIELDS and value
        }
        if legacy_secrets:
            # Migration (no-plaintext-at-rest): adopt the legacy secrets and
            # rewrite the file with them ENCRYPTED (scrubbing the plaintext).
            # When encryption is unavailable the file is left untouched —
            # destroying the only copy would be worse than leaving plaintext.
            self._runtime_secrets.update(legacy_secrets)
            if self._box.available:
                self._write(settings, corpus)
                logger.info(
                    "migrated plaintext secrets from %s to encrypted storage",
                    self._path,
                )
            else:
                logger.warning(
                    "legacy plaintext secrets found in %s but secret persistence is "
                    "disabled; adopted for this process only, file left unchanged",
                    self._path,
                )
        self._runtime_secrets.update(stored)
        return {
            "settings": settings,
            "secrets": dict(self._runtime_secrets),
            "corpus": corpus,
            "secrets_unreadable": list(self.secrets_unreadable),
        }

    def _decrypt_stored(self, encrypted: Any) -> dict[str, str]:
        """Decrypt the file's ``secrets_encrypted`` section.

        Undecryptable entries are collected in ``secrets_unreadable`` (the
        stored ciphertext is preserved for a future key restore) and skipped.
        """
        self.secrets_unreadable = []
        if not isinstance(encrypted, dict) or not encrypted:
            return {}
        decrypted: dict[str, str] = {}
        for key, entry in encrypted.items():
            if key not in SECRET_FIELDS or not isinstance(entry, dict):
                continue
            token = str(entry.get("data") or "")
            if not token:
                continue
            try:
                decrypted[key] = self._box.decrypt(token)
            except InvalidToken:
                self.secrets_unreadable.append(key)
            except RuntimeError:  # box unavailable
                break
        if self.secrets_unreadable:
            logger.error(
                "stored secret(s) %s in %s cannot be decrypted with the current "
                "master key; the stored data is preserved, the environment "
                "values apply instead. Restore the previous NYAYA_SECRET_KEY / "
                "secret.key to recover them.",
                ", ".join(sorted(self.secrets_unreadable)),
                self._path,
            )
        return decrypted

    def save(
        self,
        settings: dict[str, Any],
        secrets: dict[str, Any],
        corpus: dict[str, Any] | None = None,
    ) -> None:
        """Persist whitelisted non-secret settings (+ corpus manifest) and
        the console secrets as Fernet ciphertext, atomically.

        A store with an empty path skips the disk write (persistence
        disabled); changes then apply in-memory only.
        """
        self._runtime_secrets = {
            key: value for key, value in secrets.items() if key in SECRET_FIELDS and value
        }
        if not str(self._path) or not self._path.name:
            return
        clean_settings = {k: v for k, v in settings.items() if k in EDITABLE_FIELDS}
        self._write(clean_settings, corpus or {})

    def _write(self, settings: dict[str, Any], corpus: dict[str, Any]) -> None:
        """Atomic file write — secrets appear only as ciphertext, never plaintext."""
        encrypted: dict[str, dict[str, Any]] = {}
        if self._box.available:
            for key, value in sorted(self._runtime_secrets.items()):
                if value:
                    encrypted[key] = {"v": 1, "data": self._box.encrypt(value)}
            # Fail-safe (D-098): entries the current key cannot decrypt are
            # carried over VERBATIM — dropping them would destroy the only
            # copy. Re-entering the key (encrypted with the current key)
            # replaces them; restoring the old key recovers them.
            if self.secrets_unreadable:
                existing = self._existing_ciphertext()
                for key in self.secrets_unreadable:
                    if key not in encrypted and key in existing:
                        encrypted[key] = existing[key]
        else:
            # Fail-safe (D-098): with no usable master key we cannot
            # re-encrypt, so whatever ciphertext is already on disk is
            # carried over VERBATIM — a save must never overwrite (and thus
            # destroy) stored secrets the operator can still recover by
            # restoring the key. Newly entered keys stay memory-only.
            encrypted = self._existing_ciphertext()
            if self._runtime_secrets:
                logger.warning(
                    "secret persistence is disabled (%s); console secrets apply to "
                    "this process only and will be lost on restart",
                    self._box.disabled_reason or "no master key",
                )
        payload = {
            "settings": settings,
            "secrets": {},  # legacy plaintext section: always empty now
            "secrets_encrypted": encrypted,
            "corpus": corpus,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def _existing_ciphertext(self) -> dict[str, dict[str, Any]]:
        """Return the file's current ``secrets_encrypted`` section as-is.

        Used only on the persistence-disabled path so a settings save
        preserves stored ciphertext it cannot rewrite. Unreadable/absent
        file → empty dict (nothing to preserve).
        """
        if not self._path.is_file():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        existing = raw.get("secrets_encrypted")
        return existing if isinstance(existing, dict) else {}

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
