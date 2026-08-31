"""Language service (multilingual support, D-077).

One seam for everything language-related in the chat pipeline:

* ``resolve`` — the effective answer language for a request (manual
  selection overrides detection; ``auto`` uses script detection);
* ``refusal`` — the code-controlled specification refusal, translated
  per language (never model-generated);
* ``answer_instruction`` — the target-language instruction appended to
  the generation system prompt;
* ``translate_query`` — English translation of a non-English query for
  retrieval routing ONLY. The translation never becomes legal evidence:
  retrieval, the corpus, the citation guard, and every citation label
  stay tied to the authoritative English corpus.

Translation backends:

* default — the already-configured local LLM provider (Ollama) with a
  strict translate-only prompt;
* optional — AI4Bharat IndicTrans2 (``indictrans2_model_dir`` set):
  free/open-source (MIT), higher quality, but ~1.2 GB per direction and
  GPU-class resources. NOT installed by default; the normal deployment
  must stay usable without model downloads.

No paid APIs, no cloud translation services.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.domain.models import MessageRole
from app.language.detection import detect_language
from app.language.models import LANGUAGE_NAMES, LanguageCode
from app.llm.base import ChatMessage, GenerationRequest, LLMProvider

logger = logging.getLogger(__name__)

#: The specification refusal (A4-012), translated per language. Produced by
#: code, never by the model, so it can never be paraphrased away.
REFUSAL_RESPONSES: dict[LanguageCode, str] = {
    LanguageCode.EN: "I don't know based on the available source material.",
    LanguageCode.HI: "उपलब्ध स्रोत सामग्री के आधार पर मुझे यह जानकारी नहीं है।",
    LanguageCode.BN: "উপলব্ধ উৎস উপকরণের ভিত্তিতে আমি এটি জানি না।",
    LanguageCode.MR: "उपलब्ध स्रोत सामग्रीच्या आधारावर मला हे माहीत नाही.",
    LanguageCode.GU: "ઉપલબ્ધ સ્રોત સામગ્રીના આધારે મને આ ખબર નથી.",
    LanguageCode.TA: "கிடைக்கக்கூடிய மூலப் பொருளின் அடிப்படையில் எனக்கு இது தெரியவில்லை.",
    LanguageCode.TE: "అందుబాటులో ఉన్న మూల సామగ్రి ఆధారంగా నాకు ఇది తెలియదు.",
    LanguageCode.KN: "ಲಭ್ಯವಿರುವ ಮೂಲ ಸಾಮಗ್ರಿ ಆಧರಿಸಿ ನನಗೆ ಇದು ಗೊತ್ತಿಲ್ಲ.",
    LanguageCode.ML: "ലഭ്യമായ മൂല സാമഗ്രിയുടെ അടിസ്ഥാനത്തിൽ എനിക്കറിയില്ല.",
    LanguageCode.PA: "ਉਪਲਬਧ ਸਰੋਤ ਸਮੱਗਰੀ ਦੇ ਆਧਾਰ 'ਤੇ ਮੈਨੂੰ ਇਹ ਪਤਾ ਨਹੀਂ ਹੈ।",
    LanguageCode.OR: "ଉପଲବ୍ଧ ମୂଳ ସାମଗ୍ରୀ ଆଧାରରେ ମୋତେ ଏହା ଜଣା ନାହିଁ।",
    LanguageCode.AS: "উপলব্ধ উৎস সামগ্ৰীৰ ভিত্তিতে মই এইটো নাজানো।",
}

_TRANSLATION_SYSTEM_PROMPT = (
    "You are a deterministic translation engine for search queries. "
    "Translate the user's message into English. Output ONLY the English "
    "translation — no explanations, no answers, no added content. Keep all "
    "numbers, section numbers, and identifiers exactly as written. If the "
    "message is already in English, output it unchanged."
)


def _language_instruction(language: LanguageCode) -> str:
    name = LANGUAGE_NAMES[language]
    return (
        f"ANSWER LANGUAGE: {name}. Write the entire answer in {name}. "
        "Keep every citation label exactly as it appears in the evidence "
        "(for example [BNS s.103] or [Document <id> p.2]): never translate, "
        "reorder, or alter citation labels, act short codes, section "
        "numbers, or page numbers. When referring to a section, include the "
        "section number in the sentence."
    )


def answer_instruction(language: LanguageCode) -> str | None:
    """System-prompt instruction for the answer language (None = English)."""
    if language == LanguageCode.EN:
        return None
    return _language_instruction(language)


class LanguageService:
    """Language handling seam used by the chat pipeline (D-077)."""

    def __init__(self, detector: Callable[[str], LanguageCode] | None = None) -> None:
        self._detector = detector or detect_language

    # -- detection -----------------------------------------------------------

    def detect(self, message: str) -> LanguageCode:
        """Detected input language (English for Latin/unknown scripts)."""
        return self._detector(message)

    def resolve(self, preference: str, message: str) -> LanguageCode:
        """Effective answer language: manual selection overrides detection."""
        if preference != "auto":
            try:
                return LanguageCode(preference)
            except ValueError:
                logger.warning(
                    "unknown language preference; falling back to detection",
                    extra={"event": "language_preference_invalid", "preference": preference},
                )
        return self.detect(message)

    # -- generation ----------------------------------------------------------

    def refusal(self, language: LanguageCode) -> str:
        """Code-controlled refusal text in the requested language."""
        return REFUSAL_RESPONSES[language]

    def is_refusal_text(self, answer: str) -> bool:
        """True when the text is the specification refusal in any language."""
        stripped = answer.strip()
        return any(stripped == text for text in REFUSAL_RESPONSES.values())

    def answer_instruction(self, language: LanguageCode) -> str | None:
        """System-prompt instruction for the answer language (None = English)."""
        return answer_instruction(language)

    # -- query translation ---------------------------------------------------

    async def translate_query(
        self, provider: LLMProvider, message: str, source: LanguageCode
    ) -> str | None:
        """Translate a non-English query to English for retrieval routing.

        The result feeds ONLY route/intent detection and retrieval; the
        generation prompt keeps the user's original question. Returns None
        on any failure — the caller then retrieves with the original
        message (which finds nothing and refuses, the conservative
        outcome), never an ungrounded answer.
        """
        request = GenerationRequest(
            messages=[
                ChatMessage(role=MessageRole.SYSTEM, content=_TRANSLATION_SYSTEM_PROMPT),
                ChatMessage(role=MessageRole.USER, content=message),
            ]
        )
        try:
            result = await provider.generate(request)
        except Exception:
            logger.warning(
                "query translation failed; using original message",
                extra={"event": "language_translation_failed", "language": source.value},
            )
            return None
        translated = result.text.strip()
        if not translated or len(translated) > max(2000, len(message) * 4):
            logger.warning(
                "query translation empty or implausible; using original message",
                extra={"event": "language_translation_invalid", "language": source.value},
            )
            return None
        return translated


class IndicTrans2Backend:
    """Optional AI4Bharat IndicTrans2 translation backend (documented seam).

    Setup (NOT installed by default)::

        # ~2.4 GB models (en→Indic and Indic→en), MIT license (AI4Bharat);
        # GPU strongly recommended (CPU: ~1-4 s per query).
        git lfs install
        git clone https://github.com/AI4Bharat/IndicTrans2
        # follow the repo's model download instructions, then:
        #   settings: indictrans2_model_dir = "<path>"

    Enables offline, license-clean translation without the LLM round trip.
    Raises a clear configuration error when the model directory is absent.
    """

    def __init__(self, model_dir: str) -> None:
        try:
            from indictrans2 import Transformer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "indictrans2_model_dir is set but the IndicTrans2 package is "
                "not installed (see docs/DECISIONS.md D-077 for setup)"
            ) from exc
        self._model = Transformer(model_dir)  # pragma: no cover

    def translate(self, text: str, source: LanguageCode) -> str:  # pragma: no cover
        translated: str = self._model.translate(text, src_lang=source.value, tgt_lang="en")
        return translated
