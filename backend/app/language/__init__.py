"""Language layer for multilingual Indian-language support (D-077)."""

from app.language.conversation import multilingual_category, multilingual_reply
from app.language.detection import detect_language
from app.language.models import (
    LANGUAGE_NAMES,
    LANGUAGE_PREFERENCES,
    LanguageCode,
)
from app.language.service import (
    REFUSAL_RESPONSES,
    IndicTrans2Backend,
    LanguageService,
)

__all__ = [
    "LANGUAGE_NAMES",
    "LANGUAGE_PREFERENCES",
    "REFUSAL_RESPONSES",
    "IndicTrans2Backend",
    "LanguageCode",
    "LanguageService",
    "detect_language",
    "multilingual_category",
    "multilingual_reply",
]
