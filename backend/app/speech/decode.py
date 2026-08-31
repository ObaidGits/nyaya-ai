"""Audio decoding shared by STT providers (D-079).

Browser uploads arrive as WebM/Opus (MediaRecorder), which libsndfile cannot
decode. Strategy: try soundfile first (fast path for WAV/FLAC), then fall
back to a local ``ffmpeg`` process rendering 16 kHz mono f32 WAV, which
covers every browser recording format (webm/opus, ogg/opus, mp4/aac, mp3).
"""

from __future__ import annotations

import io
import shutil
import subprocess
from typing import Any

from app.speech.base import AudioDecodeError

ffmpeg_path: str | None = None


def _ffmpeg_available() -> str | None:
    global ffmpeg_path
    if ffmpeg_path is None:
        ffmpeg_path = shutil.which("ffmpeg") or ""
    return ffmpeg_path or None


def decode_to_wav_pcm16k_mono(data: bytes) -> Any:
    """Decode arbitrary audio bytes to a mono float tensor at 16 kHz."""
    try:
        import soundfile as sf  # type: ignore[import-untyped]
        import torch

        try:
            array, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
        except Exception:
            # soundfile cannot parse this container (e.g. WebM/Opus from a
            # browser). Use the ffmpeg fallback below instead.
            array = None
        if array is not None:
            if array.ndim == 2:  # stereo -> mono
                array = array.mean(axis=1)
            waveform = torch.from_numpy(array).unsqueeze(0)
            if int(sample_rate) != 16000:
                waveform = _resample(waveform, int(sample_rate), 16000)
            return waveform.squeeze(0)
    except AudioDecodeError:
        raise
    except Exception:  # pragma: no cover - falls through to ffmpeg
        pass

    return _decode_via_ffmpeg(data)


def _decode_via_ffmpeg(data: bytes) -> Any:
    """Render to 16 kHz mono f32 WAV via ffmpeg, then read with soundfile."""
    ffmpeg = _ffmpeg_available()
    if ffmpeg is None:
        raise AudioDecodeError(
            "The uploaded audio could not be decoded. This instance does not "
            "support the uploaded audio format."
        )
    try:
        import soundfile as sf  # type: ignore[import-untyped]
        import torch

        rendered = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
        array, sample_rate = sf.read(io.BytesIO(rendered), dtype="float32")
        waveform = torch.from_numpy(array).unsqueeze(0)
        if int(sample_rate) != 16000:
            waveform = _resample(waveform, int(sample_rate), 16000)
        return waveform.squeeze(0)
    except AudioDecodeError:
        raise
    except Exception as exc:
        raise AudioDecodeError("The uploaded audio could not be decoded.") from exc


def _resample(waveform: Any, source_rate: int, target_rate: int) -> Any:
    try:
        import torchaudio  # type: ignore[import-untyped]

        return torchaudio.functional.resample(waveform, source_rate, target_rate)
    except Exception as exc:
        raise AudioDecodeError("The uploaded audio could not be resampled to 16 kHz.") from exc
