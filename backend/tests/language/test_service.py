"""LanguageService tests: resolve, refusal, instruction, translation (D-077)."""

from __future__ import annotations

from app.language.models import LanguageCode
from app.language.service import (
    REFUSAL_RESPONSES,
    LanguageService,
    answer_instruction,
)
from tests.generation.fixtures import FailingProvider, ScriptedProvider

ENGLISH_REFUSAL = "I don't know based on the available source material."


def test_resolve_manual_selection_overrides_detection() -> None:
    service = LanguageService()
    assert service.resolve("hi", "What is the punishment for murder?") is LanguageCode.HI


def test_resolve_auto_uses_detection() -> None:
    service = LanguageService()
    assert service.resolve("auto", "धारा 103 क्या कहती है?") is LanguageCode.HI
    assert service.resolve("auto", "What is murder?") is LanguageCode.EN


def test_resolve_unknown_preference_falls_back_to_detection() -> None:
    # The API layer validates the preference; the service still fails safe.
    service = LanguageService()
    assert service.resolve("fr", "ধারা 103 কী বলে?") is LanguageCode.BN


def test_refusal_is_code_controlled_and_translated() -> None:
    service = LanguageService()
    assert service.refusal(LanguageCode.EN) == ENGLISH_REFUSAL
    for language in REFUSAL_RESPONSES:
        text = service.refusal(language)
        assert text
        if language is not LanguageCode.EN:
            assert text != ENGLISH_REFUSAL
    assert len(REFUSAL_RESPONSES) == 12  # every supported language covered


def test_answer_instruction_is_none_for_english() -> None:
    assert answer_instruction(LanguageCode.EN) is None


def test_answer_instruction_pins_citation_labels() -> None:
    instruction = answer_instruction(LanguageCode.HI)
    assert instruction is not None
    assert "Hindi" in instruction
    assert "ANSWER LANGUAGE" in instruction
    # Citation labels must survive translation untouched.
    assert "never translate" in instruction
    assert "[BNS s.103]" in instruction


async def test_translate_query_uses_dedicated_translate_only_prompt() -> None:
    provider = ScriptedProvider(["What is the punishment for murder?"])
    service = LanguageService()
    translated = await service.translate_query(provider, "मर्डर की सजा क्या है?", LanguageCode.HI)
    assert translated == "What is the punishment for murder?"
    assert len(provider.requests) == 1
    system = provider.requests[0].messages[0].content
    user = provider.requests[0].messages[1].content
    assert "translation engine" in system
    assert "ONLY the English translation" in system
    assert user == "मर्डर की सजा क्या है?"  # original message, not altered


async def test_translate_query_returns_none_on_provider_failure() -> None:
    service = LanguageService()
    assert (
        await service.translate_query(FailingProvider(), "मर्डर की सजा क्या है?", LanguageCode.HI)
        is None
    )


async def test_translate_query_returns_none_on_empty_response() -> None:
    service = LanguageService()
    assert (
        await service.translate_query(ScriptedProvider([""]), "मर्डर की सजा क्या है?", LanguageCode.HI)
        is None
    )


async def test_translate_query_returns_none_on_implausible_length() -> None:
    # A "translation" several times longer than the input is not a
    # translation; treat it as failure so the caller fails closed.
    service = LanguageService()
    assert (
        await service.translate_query(
            ScriptedProvider(["word " * 500]), "छोटा प्रश्न", LanguageCode.HI
        )
        is None
    )


def test_is_refusal_text_matches_every_language() -> None:
    service = LanguageService()
    for language, text in REFUSAL_RESPONSES.items():
        assert service.is_refusal_text(text), language
    assert not service.is_refusal_text("Murder is punishable with death [TS s.103].")
