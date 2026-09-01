"""LanguageService tests: resolve, refusal, instruction, translation (D-077)."""

from __future__ import annotations

from app.language.acts import mentioned_acts, replace_act_mentions
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


async def test_translate_query_repairs_re_branded_act() -> None:
    # D-094 live incident: the translator rendered "भारतीय न्याय संहिता" as
    # "Indian Penal Code"; the foreign-statute guard then refused an
    # in-scope question. The repair rewrites the substituted mention back
    # to the act the user actually named.
    provider = ScriptedProvider(["What does Section 303 of the Indian Penal Code say?"])
    service = LanguageService()
    translated = await service.translate_query(
        provider, "भारतीय न्याय संहिता की धारा 303 क्या कहती है?", LanguageCode.HI
    )
    assert translated == "What does Section 303 of the Bharatiya Nyaya Sanhita say?"


async def test_translate_query_appends_act_dropped_by_translation() -> None:
    # The model dropped the act name entirely: restore it so routing and
    # retrieval still see the authority the user asked about.
    provider = ScriptedProvider(["What does section 303 say?"])
    service = LanguageService()
    translated = await service.translate_query(
        provider, "भारतीय न्याय संहिता की धारा 303 क्या कहती है?", LanguageCode.HI
    )
    assert "Bharatiya Nyaya Sanhita" in translated
    assert translated.startswith("What does section 303 say?")


async def test_translate_query_keeps_genuine_ipc_question() -> None:
    # A Hindi question that genuinely asks about the IPC must keep the IPC
    # mention — the foreign-statute guard should still fail closed on it.
    provider = ScriptedProvider(["What does Section 303 of the Indian Penal Code say?"])
    service = LanguageService()
    translated = await service.translate_query(
        provider, "भारतीय दंड संहिता की धारा 303 क्या कहती है?", LanguageCode.HI
    )
    assert translated == "What does Section 303 of the Indian Penal Code say?"


async def test_translate_query_no_act_mentions_untouched() -> None:
    provider = ScriptedProvider(["What is the punishment for murder?"])
    service = LanguageService()
    translated = await service.translate_query(provider, "मर्डर की सजा क्या है?", LanguageCode.HI)
    assert translated == "What is the punishment for murder?"


async def test_translation_prompt_forbids_act_substitution() -> None:
    provider = ScriptedProvider(["What is the punishment for murder?"])
    service = LanguageService()
    await service.translate_query(provider, "मर्डर की सजा क्या है?", LanguageCode.HI)
    system = provider.requests[0].messages[0].content
    assert "never substitute" in system
    assert "Bharatiya Nyaya Sanhita" in system


def test_mentioned_acts_detects_aliases_across_scripts() -> None:
    assert mentioned_acts("भारतीय न्याय संहिता की धारा 303") == {"Bharatiya Nyaya Sanhita"}
    assert mentioned_acts("What does BNS s.303 say?") == {"Bharatiya Nyaya Sanhita"}
    assert mentioned_acts("IPC and CrPC both apply") == {
        "Indian Penal Code",
        "Code of Criminal Procedure",
    }
    # Token matching: "ipcs" is not the IPC.
    assert mentioned_acts("my ipcs are broken") == set()
    assert mentioned_acts("ordinary question") == set()


def test_replace_act_mentions_rewrites_only_target_statute() -> None:
    out = replace_act_mentions(
        "Section 303 of the Indian Penal Code, read with CrPC",
        {"Indian Penal Code": "Bharatiya Nyaya Sanhita"},
    )
    assert out == "Section 303 of the Bharatiya Nyaya Sanhita, read with CrPC"
