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


@pytest.mark.parametrize(
    ("query", "route"),
    [
        # Procedural mentions of FIR/complaint/notice are statute questions.
        ("What if a police officer refuses to file my FIR?", RetrievalRoute.STATUTE),
        ("How do I file my FIR?", RetrievalRoute.STATUTE),
        (
            "My neighbour filed a false complaint against me. What law applies?",
            RetrievalRoute.STATUTE,
        ),
        ("The police lost my FIR. What can I do?", RetrievalRoute.STATUTE),
        ("How do I give notice to my landlord?", RetrievalRoute.STATUTE),
        ("The police refused to register the FIR. What remedy do I have?", RetrievalRoute.STATUTE),
        # Questions about the artifact's content still route to documents.
        ("What does my uploaded FIR say?", RetrievalRoute.DOCUMENT),
        ("Summarize my FIR.", RetrievalRoute.DOCUMENT),
        ("What does my notice say?", RetrievalRoute.DOCUMENT),
        ("What does my document say?", RetrievalRoute.DOCUMENT),
        ("Explain this agreement.", RetrievalRoute.DOCUMENT),
        ("What does my uploaded file contain?", RetrievalRoute.DOCUMENT),
        ("What does the attached document say?", RetrievalRoute.DOCUMENT),
        # Possessive document noun + section reference stays combined.
        ("Does my notice comply with section 35 BNS?", RetrievalRoute.COMBINED),
    ],
)
def test_filing_procedure_vs_document_content(query: str, route: RetrievalRoute) -> None:
    assert classify_route(query) == route


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


# ---------------------------------------------------------------------------
# Legal-artifact nouns (§14 remediation — live regression)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "What is the filing date in the writ petition?",
        "What does the petition pray for?",
        "What relief does the plaint seek?",
        "Summarize the suit filed by the plaintiff.",
        "What does the affidavit state?",
        "What did the judgment hold?",
    ],
)
def test_legal_artifact_nouns_route_document(query: str) -> None:
    """Petition/writ/suit/plaint/affidavit/judgment are document artifacts:
    a question about them must route to the user's uploads, not the statute
    corpus. Live regression: the writ-petition question routed STATUTE and
    refused while the upload was READY."""
    assert classify_route(query) == RetrievalRoute.DOCUMENT


def test_petition_noun_still_statute_when_procedural() -> None:
    """Filing-verb procedure keeps the statute route: "draft a petition"
    asks about procedure, not an uploaded artifact's content."""
    assert classify_route("How do I file a petition?") == RetrievalRoute.STATUTE


@pytest.mark.parametrize(
    "query",
    [
        "What is this uploaded doc for?",
        "What is this doc about?",
        "Summarize my docs",
        "Show me that uploaded doc",
    ],
)
def test_colloquial_doc_noun_routes_document(query: str) -> None:
    """The colloquial "doc"/"docs" abbreviation is a document reference.
    Live regression: "What is this uploaded doc for?" routed STATUTE; the
    statute corpus then scored the generic phrasing ~0.58 cosine — above
    its sufficiency threshold — so the document fallback never fired and
    the model refused on irrelevant statute chunks."""
    assert classify_route(query) == RetrievalRoute.DOCUMENT
