"""Intent detection + routing tests (A3-012, A3-015)."""

import pytest
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


def test_bare_number_section_intent() -> None:
    """A bare number in a question is a section reference (A3-014)."""
    intent = detect_section_intent("What does 103 say?")
    assert intent is not None
    assert intent.section_number == "103"


def test_bare_number_with_subsection_intent() -> None:
    intent = detect_section_intent("Explain 103(1)")
    assert intent is not None
    assert intent.section_number == "103"
    assert intent.subsection == "1"


@pytest.mark.parametrize(
    "query",
    [
        "detention beyond 24 hours",
        "7 years imprisonment",
        "500 rupees fine",
        "30 days notice period",
        "2 lakh crore scam",
        "my case no. 42 status",
        "page 12 of the judgment",
        "article 21 of the constitution",
        "fir 302 details",
        "chapter 5 contents",
        "form 45 requirements",
        "7.5 lakh compensation",
        "ip 169.254.169.254 metadata",
        "version 1.2.3 of the rules",
    ],
)
def test_quantities_and_identifiers_are_not_section_intents(query: str) -> None:
    assert detect_section_intent(query) is None


def test_route_statute_default() -> None:
    assert classify_route("What is bail?") == RetrievalRoute.STATUTE


def test_route_document() -> None:
    assert classify_route("What does my notice say?") == RetrievalRoute.DOCUMENT


def test_route_combined() -> None:
    assert classify_route("Does my notice comply with section 35 BNS?") == RetrievalRoute.COMBINED


def test_gujarati_and_odia_digit_sections_take_lookup_path() -> None:
    # Regression (D-077 audit): all supported-script digit forms must
    # reach the deterministic section lookup, not just Devanagari/Bengali.
    gujarati = detect_section_intent("કલમ ૧૦૩ માં શું જોગવાઈ છે?")
    assert gujarati is not None
    assert gujarati.section_number == "103"
    odia = detect_section_intent("ଧାରା ୧୦୩ ରେ କଣ ଅଛି?")
    assert odia is not None
    assert odia.section_number == "103"


def test_marathi_section_query_is_marathi() -> None:
    # Route/intent is script-based and unaffected by hi/mr disambiguation,
    # but this pins the Devanagari lookup for the Marathi spec prompt.
    intent = detect_section_intent("कलम १०३ मध्ये काय तरतूद आहे?")
    assert intent is not None
    assert intent.section_number == "103"
