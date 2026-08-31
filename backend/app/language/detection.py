"""Language detection (multilingual support, D-077).

Default backend: deterministic Unicode-script detection. Zero dependencies,
zero model downloads, instant, and fully explainable — well suited to the
supported set, where each language (except the Devanagari and Bengali script
pairs) has a distinct script.

Script pairs the default backend cannot split by script alone:

* Devanagari → Hindi vs Marathi (both use Devanagari; resolved by a
  deterministic lexical marker count — see ``_devanagari_language``);
* Bengali script → Bengali vs Assamese (Assamese adds ৰ / ৱ).

Auto-detect resolves Bengali script to Assamese only when
Assamese-specific characters are present; a manual selector choice
always overrides the detection (frontend contract).

``FastTextLanguageDetector`` is the optional statistical backend
(``language_detection_backend = "fasttext"``): it requires the fastText
package and a downloaded ``lid.176.bin`` model (~130 MB, CC BY-SA 4.0)
and distinguishes hi/mr and bn/as lexically. It is not the default
because the normal deployment must not require model downloads.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar

from app.language.models import LanguageCode

logger = logging.getLogger(__name__)

# Unicode block boundaries per script (inclusive start, exclusive end).
_SCRIPT_BLOCKS: dict[str, tuple[int, int]] = {
    "devanagari": (0x0900, 0x0980),
    "bengali_script": (0x0980, 0x0A00),
    "gurmukhi": (0x0A00, 0x0A80),
    "gujarati": (0x0A80, 0x0B00),
    "odia": (0x0B00, 0x0B80),
    "tamil": (0x0B80, 0x0C00),
    "telugu": (0x0C00, 0x0C80),
    "kannada": (0x0C80, 0x0D00),
    "malayalam": (0x0D00, 0x0D80),
}

_SCRIPT_TO_LANGUAGE: dict[str, LanguageCode] = {
    "devanagari": LanguageCode.HI,
    "gurmukhi": LanguageCode.PA,
    "gujarati": LanguageCode.GU,
    "odia": LanguageCode.OR,
    "tamil": LanguageCode.TA,
    "telugu": LanguageCode.TE,
    "kannada": LanguageCode.KN,
    "malayalam": LanguageCode.ML,
}

# Assamese-specific characters (Bengali script block): ৰ (U+09F0), ৱ (U+09F1).
_ASSAMESE_CHARS = frozenset("ৰৱ")

# Hindi vs Marathi share the Devanagari script. Script detection cannot
# split them, but the two languages are lexically distinct where it
# matters (copula, negation, and core vocabulary), so a deterministic
# marker count disambiguates auto-detect ("कायदा... आहे" is unambiguously
# Marathi; "है/हैं/नहीं/क्या" is unambiguously Hindi). Substring counts
# cover inflected forms ("कायद" matches "कायद्याविषयी"); a tie keeps the
# documented default, Hindi. Markers are deliberately non-legal-word
# function vocabulary, so they occur in any message of the language.
_MARATHI_MARKERS = (
    "आहे",
    "आहेत",
    "आहेस",
    "आहात",
    "आहोत",
    "नाही",
    "कायद",
    "काय",
    "तुम्ही",
    "मला",
    "माझ",
    "मध्ये",
    "शकत",
    "केले",
    "दिले",
    "झाले",
    "कोण",
    "करू",
)
_HINDI_MARKERS = (
    "है",
    "हैं",
    "हूँ",
    "होगा",
    "नहीं",
    "क्या",
    "कौन",
    "मैं",
    "कानून",
    "सकत",
    "में",
    "बताओ",
    "बताइए",
    "पूछ",
)


def _devanagari_language(text: str) -> LanguageCode:
    """Hindi or Marathi: score discriminative markers (tie → Hindi)."""
    marathi = sum(text.count(marker) for marker in _MARATHI_MARKERS)
    hindi = sum(text.count(marker) for marker in _HINDI_MARKERS)
    return LanguageCode.MR if marathi > hindi else LanguageCode.HI


_LATIN_UPPER = 0x0041
_LATIN_LOWER_END = 0x007B


def _script_of(char: str) -> str | None:
    code = ord(char)
    if _LATIN_UPPER <= code < _LATIN_LOWER_END:
        return "latin"
    for script, (start, end) in _SCRIPT_BLOCKS.items():
        if start <= code < end:
            return script
    return None


def detect_language(text: str) -> LanguageCode:
    """Detect the dominant Indic script, or English for Latin/other text.

    Latin-script input (including romanized Indic languages) resolves to
    English — the conservative default that keeps the existing English
    pipeline byte-identical. An Indic script wins only when it is the
    majority of the letters in the message, so mixed-language input like
    "What does धारा 103 say?" stays English.
    """
    counts: dict[str, int] = {}
    assamese_chars = 0
    for char in text:
        script = _script_of(char)
        if script is None:
            continue
        counts[script] = counts.get(script, 0) + 1
        if script == "bengali_script" and char in _ASSAMESE_CHARS:
            assamese_chars += 1

    indic = {script: count for script, count in counts.items() if script != "latin"}
    if not indic:
        return LanguageCode.EN
    dominant_script = max(indic, key=lambda script: indic[script])
    dominant_count = indic[dominant_script]
    latin_count = counts.get("latin", 0)
    # The Indic script must not be a tiny fragment of a Latin message.
    if dominant_count < 3 or dominant_count < latin_count:
        return LanguageCode.EN
    if dominant_script == "bengali_script":
        return LanguageCode.AS if assamese_chars else LanguageCode.BN
    if dominant_script == "devanagari":
        return _devanagari_language(text)
    return _SCRIPT_TO_LANGUAGE[dominant_script]


class FastTextLanguageDetector:
    """Optional statistical detector over fastText's lid.176 model.

    Setup (documented, NOT installed by default)::

        pip install fasttext-wheel
        wget https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin

        # Settings
        language_detection_backend = "fasttext"
        fasttext_model_path = "/path/to/lid.176.bin"

    Model ~130 MB, license CC BY-SA 4.0. Distinguishes hi/mr and bn/as
    lexically, which script detection cannot. Raises a clear configuration
    error when the package or model file is missing.
    """

    _SUPPORTED_LABELS: ClassVar[dict[str, LanguageCode]] = {
        "en": LanguageCode.EN,
        "hi": LanguageCode.HI,
        "bn": LanguageCode.BN,
        "mr": LanguageCode.MR,
        "gu": LanguageCode.GU,
        "ta": LanguageCode.TA,
        "te": LanguageCode.TE,
        "kn": LanguageCode.KN,
        "ml": LanguageCode.ML,
        "pa": LanguageCode.PA,
        "or": LanguageCode.OR,
        "as": LanguageCode.AS,
    }

    def __init__(self, model_path: str) -> None:
        try:
            import fasttext  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "language_detection_backend='fasttext' requires the fasttext "
                "package (pip install fasttext-wheel)"
            ) from exc
        self._model = fasttext.load_model(model_path)  # pragma: no cover

    def detect(self, text: str) -> LanguageCode:
        """Return the detected language, or English for out-of-set labels."""
        label = self._predict(text)
        return self._SUPPORTED_LABELS.get(label, LanguageCode.EN)

    def _predict(self, text: str) -> str:  # pragma: no cover - model-dependent
        labels, _scores = self._model.predict(text.replace("\n", " "))
        return labels[0].removeprefix("__label__") if labels else "en"


def detector_for_backend(backend: str, model_path: str | None) -> Callable[[str], LanguageCode]:
    """Build the configured detector callable (default: script detection)."""
    if backend == "fasttext":
        if not model_path:
            raise RuntimeError("fasttext detection requires fasttext_model_path")
        detector = FastTextLanguageDetector(model_path)
        return detector.detect
    return detect_language
