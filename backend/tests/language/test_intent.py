"""Indic intent routing tests (D-077): section lookup + document hints."""

from __future__ import annotations

from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import RetrievalRoute


def test_hindi_section_query_takes_deterministic_lookup() -> None:
    intent = detect_section_intent("धारा 103 में क्या प्रावधान है?")
    assert intent is not None
    assert intent.section_number == "103"


def test_bengali_digits_are_normalized() -> None:
    intent = detect_section_intent("ধারা ১০৩ কী বলে?")
    assert intent is not None
    assert intent.section_number == "103"


def test_indic_document_hint_routes_to_documents() -> None:
    assert classify_route("इस दस्तावेज़ का सारांश दें") is RetrievalRoute.DOCUMENT


def test_indic_document_hint_fails_closed_without_session() -> None:
    # Routing is only half the contract: without a session the document
    # route retrieves nothing and the pipeline refuses (verified end to
    # end in the chat API tests).
    assert classify_route("मेरा नोटिस क्या कहता है") is RetrievalRoute.DOCUMENT


def test_indic_section_query_routes_to_statute() -> None:
    assert classify_route("धारा 103 क्या कहती है") is RetrievalRoute.STATUTE


def test_mixed_document_and_section_is_combined() -> None:
    assert classify_route("मेरे दस्तावेज़ में धारा 103 क्या कहता है") is RetrievalRoute.COMBINED


def test_english_routing_unchanged() -> None:
    assert classify_route("What does section 103 BNS say?") is RetrievalRoute.STATUTE
    assert classify_route("What does my notice say?") is RetrievalRoute.DOCUMENT
    assert classify_route("What is murder?") is RetrievalRoute.STATUTE
