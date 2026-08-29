"""Section-intent detection and query routing (ARCHITECTURE §13-§14, D-017).

* ``detect_section_intent`` — deterministic regex detection of direct
  section-number queries ("What is section 103 BNS?", "BNS 103(1)?").
* ``classify_route`` — statute / document / combined routing.

The router is keyword-based and deliberately conservative: a query is only
routed away from the statute corpus when it references the user's own
document ("my notice", "uploaded document", "this agreement", ...).
"""

from __future__ import annotations

import re

from app.retrieval.models import RetrievalRoute, SectionIntent

# "section 103", "section 103(1)", "s. 103", "sec. 103"
_SECTION_RE = re.compile(
    r"\b(?:section|sec\.?|s\.)\s*(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?",
    re.IGNORECASE,
)
# "103 BNS" / "BNS 103" — identifier-style phrasing
_ACT_NUMBER_RE = re.compile(r"\b(?:BNS|BNSS)\s*(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?", re.IGNORECASE)
_NUMBER_ACT_RE = re.compile(
    r"\b(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?\s*(?:BNS|BNSS)\b", re.IGNORECASE
)
_ACT_MENTION_RE = re.compile(r"\b(BNS|BNSS)\b", re.IGNORECASE)

_DOCUMENT_HINT_RE = re.compile(
    r"\b(my|this|the|uploaded|attached)\s+"
    r"(document|notice|agreement|contract|letter|complaint|fir|f\.i\.r\.|file|pdf)\b"
    r"|\bmy\s+(notice|document|agreement|file)\b",
    re.IGNORECASE,
)


def detect_section_intent(query: str) -> SectionIntent | None:
    """Return the direct-lookup intent for section-number queries.

    Supports "section 103", "section 103(2)", "BNS 103", "103 BNS".
    Returns None when no section identifier is present.
    """
    for pattern in (_SECTION_RE, _ACT_NUMBER_RE, _NUMBER_ACT_RE):
        match = pattern.search(query)
        if match:
            section = match.group(1)
            subsection = match.group(2)
            act = None
            act_mention = _ACT_MENTION_RE.search(query)
            if act_mention:
                act = act_mention.group(1).upper()
            return SectionIntent(
                act_short=act,
                section_number=section,
                subsection=subsection,
            )
    return None


def classify_route(query: str) -> RetrievalRoute:
    """Statute / document / combined routing (ARCHITECTURE §14)."""
    document = bool(_DOCUMENT_HINT_RE.search(query))
    statute = bool(_ACT_MENTION_RE.search(query)) or detect_section_intent(query) is not None
    if document and statute:
        return RetrievalRoute.COMBINED
    if document:
        return RetrievalRoute.DOCUMENT
    return RetrievalRoute.STATUTE
