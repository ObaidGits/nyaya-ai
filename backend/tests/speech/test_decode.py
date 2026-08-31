"""Audio decode tests: soundfile fast path + ffmpeg WebM/Opus fallback (D-079).

Browser MediaRecorder uploads audio/webm;codecs=opus, which libsndfile cannot
read — the ffmpeg fallback is what makes real mic recordings work.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from app.speech.base import AudioDecodeError
from app.speech.decode import decode_to_wav_pcm16k_mono

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

_WAV = Path("/tmp/e2e-audio/en.wav")


@pytest.fixture(scope="module")
def webm_opus(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Real WebM/Opus clip transcoded from the English E2E WAV."""
    wav = _WAV
    if not wav.exists():
        pytest.skip("E2E WAV fixture not generated on this machine")
    out = tmp_path_factory.mktemp("decode") / "clip.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav),
            "-c:a",
            "libopus",
            str(out),
        ],
        check=True,
    )
    return out.read_bytes()


def test_ffmpeg_fallback_decodes_webm_opus(webm_opus: bytes) -> None:
    waveform = decode_to_wav_pcm16k_mono(webm_opus)
    assert waveform.dim() == 1
    assert float(waveform.abs().max()) > 0.01  # real audio, not silence


def test_wav_fast_path_still_works(webm_opus: bytes) -> None:
    if not _WAV.exists():
        pytest.skip("E2E WAV fixture not generated on this machine")
    waveform = decode_to_wav_pcm16k_mono(_WAV.read_bytes())
    assert waveform.dim() == 1
    assert float(waveform.abs().max()) > 0.01


def test_corrupt_audio_fails_cleanly() -> None:
    with pytest.raises(AudioDecodeError):
        decode_to_wav_pcm16k_mono(b"\x00\x01\x02 not audio at all")
