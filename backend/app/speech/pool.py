"""Speech provider pool runtime (provider failover, 2026-09).

STT and TTS pools reuse the shared ``FailoverRouter``. Each entry is built
through the existing ``SpeechService`` construction path (no duplicated
provider wiring), with a per-entry Settings override so credentials, models
and endpoints are per-entry. An empty pool leaves the single-provider
ENV/browser behavior untouched.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast, overload

from pydantic import SecretStr

from app.core.config import Settings
from app.providers.health import HealthBoard
from app.providers.models import (
    PoolSecrets,
    ProviderEntryConfig,
    ProviderPoolConfig,
)
from app.providers.router import FailoverPolicy, FailoverRouter
from app.speech.base import (
    STTProvider,
    SynthesisResult,
    TranscriptionResult,
    TTSProvider,
)
from app.speech.service import SpeechService

logger = logging.getLogger(__name__)

#: Settings fields one STT/TTS entry may override.
_STT_FIELDS = ("speech_stt_provider", "speech_stt_model", "speech_stt_base_url")
_TTS_FIELDS = ("speech_tts_provider", "speech_tts_model", "speech_tts_base_url")


def _entry_settings(
    base: Settings, entry: ProviderEntryConfig, api_key: str, fields: tuple[str, ...]
) -> Settings:
    updates: dict[str, Any] = {}
    if entry.provider:
        updates[fields[0]] = entry.provider
    if entry.model:
        updates[fields[1]] = entry.model
    if entry.base_url:
        updates[fields[2]] = entry.base_url
    if api_key:
        updates["speech_stt_api_key" if "stt" in fields[0] else "speech_tts_api_key"] = SecretStr(
            api_key
        )
    return base.model_copy(update=updates)


def build_speech_entry_provider(
    base: Settings, entry: ProviderEntryConfig, api_key: str, kind: str
) -> object:
    """Build one entry's provider through the standard SpeechService path."""
    fields = _STT_FIELDS if kind == "stt" else _TTS_FIELDS
    service = SpeechService(settings=_entry_settings(base, entry, api_key, fields))
    return getattr(service, kind)  # property access triggers the build


class FailoverSTT(STTProvider):
    """STTProvider facade over the STT pool with bounded failover."""

    def __init__(
        self,
        config: ProviderPoolConfig,
        providers: dict[str, STTProvider],
        board: HealthBoard,
        policy: FailoverPolicy | None = None,
    ) -> None:
        self._providers = providers
        self._router = FailoverRouter("stt", config, self._resolve, board, policy)
        self.last_entry_id: str | None = None

    def _resolve(self, entry: ProviderEntryConfig) -> STTProvider:
        return self._providers[entry.id]

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult:
        result, entry_id = await self._router.run(
            lambda provider: provider.transcribe(data, mime_type=mime_type, language=language)
        )
        self.last_entry_id = entry_id
        return result


class FailoverTTS(TTSProvider):
    """TTSProvider facade over the TTS pool with bounded failover."""

    def __init__(
        self,
        config: ProviderPoolConfig,
        providers: dict[str, TTSProvider],
        board: HealthBoard,
        policy: FailoverPolicy | None = None,
    ) -> None:
        self._providers = providers
        self._router = FailoverRouter("tts", config, self._resolve, board, policy)
        self.last_entry_id: str | None = None

    def _resolve(self, entry: ProviderEntryConfig) -> TTSProvider:
        return self._providers[entry.id]

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        result, entry_id = await self._router.run(
            lambda provider: provider.synthesize(text, language=language)
        )
        self.last_entry_id = entry_id
        return result


@overload
def build_speech_failover(
    kind: Literal["stt"],
    config: ProviderPoolConfig,
    secrets: PoolSecrets,
    settings: Settings,
    board: HealthBoard,
    policy: FailoverPolicy | None = None,
) -> STTProvider | None: ...


@overload
def build_speech_failover(
    kind: Literal["tts"],
    config: ProviderPoolConfig,
    secrets: PoolSecrets,
    settings: Settings,
    board: HealthBoard,
    policy: FailoverPolicy | None = None,
) -> TTSProvider | None: ...


def build_speech_failover(
    kind: str,
    config: ProviderPoolConfig,
    secrets: PoolSecrets,
    settings: Settings,
    board: HealthBoard,
    policy: FailoverPolicy | None = None,
) -> STTProvider | TTSProvider | None:
    """Build the STT or TTS failover wrapper; ``None`` when pool is empty.

    ``kind`` is "stt" or "tts". Local model providers (whisper etc.) load
    weights lazily on first use, so building entries stays cheap.
    """
    if kind not in ("stt", "tts"):
        raise ValueError(f"unknown speech pool kind '{kind}'")
    providers: dict[str, object] = {}
    for entry in config.enabled_entries():
        try:
            providers[entry.id] = build_speech_entry_provider(
                settings, entry, secrets.get(kind, entry.id), kind
            )
        except Exception:
            logger.exception(
                "speech pool entry unbuildable, skipping",
                extra={"entry": entry.id, "provider": entry.provider, "kind": kind},
            )
    if not providers:
        return None
    if kind == "stt":
        stt_providers = cast("dict[str, STTProvider]", providers)
        return FailoverSTT(config, stt_providers, board, policy)
    tts_providers = cast("dict[str, TTSProvider]", providers)
    return FailoverTTS(config, tts_providers, board, policy)
