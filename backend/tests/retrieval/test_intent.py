"""Intent detection + routing tests (A3-012, A3-015)."""

from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import RetrievalRoute


def test_section_intent_plain() -> None:
    intent = detect_section_intent("What is section 103 of BNS?")
    assert intent is not None
    assert intent.section_number == "103"
    assert intent.act_short == "BNS"


def test_section_intent_with_subsection() -> None:
    intent = detect_section_intent("explain section 2(11) BNS")
    assert intent is not None
    assert intent.section_number == "2"
    assert intent.subsection == "11"


def test_section_intent_bns_prefix() -> None:
    intent = detect_section_intent("BNS 103(1) punishment?")
    assert intent is not None
    assert intent.section_number == "103"
    assert intent.subsection == "1"
    assert intent.act_short == "BNS"


def test_section_intent_number_then_act() -> None:
    intent = detect_section_intent("what does 197 BNSS say about bail")
    assert intent is not None
    assert intent.section_number == "197"
    assert intent.act_short == "BNSS"


def test_no_section_intent() -> None:
    assert detect_section_intent("law concerning causing death") is None


def test_route_statute_default() -> None:
    assert classify_route("What is bail?") == RetrievalRoute.STATUTE


def test_route_document() -> None:
    assert classify_route("What does my notice say?") == RetrievalRoute.DOCUMENT


def test_route_combined() -> None:
    assert classify_route("Does my notice comply with section 35 BNS?") == RetrievalRoute.COMBINED
