"""Speech (STT/TTS) subsystem: voice input and output layers (D-079)."""

from app.speech.base import (
    AudioDecodeError,
    EmptyTranscriptionError,
    SpeechProviderError,
    SpeechProviderNotConfiguredError,
    SpeechUnsupportedLanguageError,
    STTProvider,
    TranscriptionResult,
    TTSProvider,
)
from app.speech.service import SpeechService, create_speech_service

__all__ = [
    "AudioDecodeError",
    "EmptyTranscriptionError",
    "STTProvider",
    "SpeechProviderError",
    "SpeechProviderNotConfiguredError",
    "SpeechService",
    "SpeechUnsupportedLanguageError",
    "TTSProvider",
    "TranscriptionResult",
    "create_speech_service",
]
