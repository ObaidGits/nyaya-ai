"""Language codes and display names (multilingual support, D-077).

Twelve supported answer languages: English plus eleven major Indian
languages. The codes are the ISO 639-1 short codes used by the chat API's
``language`` preference field (``auto`` or a code below).
"""

from __future__ import annotations

from enum import StrEnum


class LanguageCode(StrEnum):
    """Supported answer languages."""

    EN = "en"
    HI = "hi"
    BN = "bn"
    MR = "mr"
    GU = "gu"
    TA = "ta"
    TE = "te"
    KN = "kn"
    ML = "ml"
    PA = "pa"
    OR = "or"
    AS = "as"


#: Display names for prompts, logs, and the frontend selector.
LANGUAGE_NAMES: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.HI: "Hindi",
    LanguageCode.BN: "Bengali",
    LanguageCode.MR: "Marathi",
    LanguageCode.GU: "Gujarati",
    LanguageCode.TA: "Tamil",
    LanguageCode.TE: "Telugu",
    LanguageCode.KN: "Kannada",
    LanguageCode.ML: "Malayalam",
    LanguageCode.PA: "Punjabi",
    LanguageCode.OR: "Odia",
    LanguageCode.AS: "Assamese",
}

#: Valid chat-request language preferences: "auto" or a supported code.
LANGUAGE_PREFERENCES: frozenset[str] = frozenset({"auto", *(code.value for code in LanguageCode)})
