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

# "section 103", "section 103(1)", "s. 103", "sec. 103". The trailing
# (?!\d) guards reject truncated 4+ digit numbers ("BNS 1030" must not
# resolve to section 103).
_SECTION_RE = re.compile(
    r"\b(?:section|sec\.?|s\.)\s*(\d{1,3})(?!\d)(?:\s*\(\s*(\d{1,3})\s*\))?(?!\d)",
    re.IGNORECASE,
)
# "103 BNS" / "BNS 103" — identifier-style phrasing
_ACT_NUMBER_RE = re.compile(
    r"\b(?:BNS|BNSS)\s*(\d{1,3})(?!\d)(?:\s*\(\s*(\d{1,3})\s*\))?(?!\d)", re.IGNORECASE
)
_NUMBER_ACT_RE = re.compile(
    r"\b(\d{1,3})(?!\d)(?:\s*\(\s*(\d{1,3})\s*\))?(?!\d)\s*(?:BNS|BNSS)\b", re.IGNORECASE
)
_ACT_MENTION_RE = re.compile(r"\b(BNS|BNSS)\b", re.IGNORECASE)

# Bare section numbers: "What does 103 say?" / "Explain 103(1)". A 1-3 digit
# number is treated as a section reference unless it is a quantity (followed
# by a unit word: "30 days", "7 years", "500 rupees"), a non-statute
# identifier (preceded by case/page/form/no./chapter/...), or part of a
# decimal/IP-style number ("7.5 lakh", "169.254.169.254").
_BARE_NUMBER_RE = re.compile(r"\b(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?(?!\w)")
# Decimal/IP continuation: digit(s) + dot on either side of the match.
_DECIMAL_TAIL_RE = re.compile(r"^\.\d")
_DECIMAL_PREFIX_RE = re.compile(r"\d\.\s*$")
_UNIT_FOLLOWER_RE = re.compile(
    r"^\s*(?:days?|years?|months?|weeks?|hours?|minutes?|seconds?|rupees?|rs\.?|%"
    r"|per\s*cent|percent|times?|persons?|people|lakh|lakhs|crore|crores|pages?"
    r"|forms?|copies?|numbers?|articles?|clauses?)\b",
    re.IGNORECASE,
)
# Non-statute identifiers and currency amounts preceding a bare number:
# "case no. 5", "page 12", "Rs 300", "$500". Currency symbols are not word
# characters, so they sit outside the \b alternation.
_NON_STATUTE_PRECEDER_RE = re.compile(
    r"(?:\b(?:case|number|no|nos|page|pg|form|fir|chapter|part|schedule|annexure"
    r"|article|clause|sub|sl|serial|rs|inr|usd)|[₹$€£])\s*\.?\s*$",
    re.IGNORECASE,
)

# Document nouns: artifacts a user may hold and ask about. Grouped so the
# hint pattern and the procedural-exception patterns below stay in sync.
# STRONG nouns are legalese that almost always mean the user's artifact
# ("the writ petition", "what did the judgment hold") — a bare "the" is a
# document hint. WEAK nouns double as ordinary English words with generic
# statute meanings ("the notice period", "the documents required for
# filing an FIR"), so they need a possessive/deictic determiner ("my
# notice", "this agreement", "the uploaded FIR") to count as hints.
_DOCUMENT_STRONG_NOUN_RE = (
    r"(?:petitions?|writs?|suits?|plaints?|affidavits?|judgements?|judgments?|firs?|f\.i\.r\.)"
)
_DOCUMENT_WEAK_NOUN_RE = (
    r"(?:documents?|docs?|notices?|agreements?|contracts?|letters?|complaints?"
    r"|files?|pdfs?|deeds?|wills?)"
)
_DOCUMENT_NOUN_RE = rf"(?:{_DOCUMENT_STRONG_NOUN_RE}|{_DOCUMENT_WEAK_NOUN_RE})"
_DOCUMENT_DEICTIC_RE = (
    r"(?:my|our|this|that|these|those|his|her|their|uploaded|attached|which|first|second"
    r"|third|fourth|fifth|1st|2nd|3rd|4th|5th|last|latest|newest|final|previous|prior"
    r"|earlier|other|another|both)"
)

# A document hint is a deictic determiner + document noun ("my notice",
# "the uploaded FIR") — or "the" + a strong artifact noun ("the
# petition") — referencing the user's own artifact.
_DOCUMENT_HINT_RE = re.compile(
    rf"\b{_DOCUMENT_DEICTIC_RE}\s+(?:\w+\s+){{0,2}}{_DOCUMENT_NOUN_RE}\b"
    rf"|\bthe\s+(?:\w+\s+){{0,2}}{_DOCUMENT_STRONG_NOUN_RE}\b",
    re.IGNORECASE,
)

# Procedural exceptions (routing audit, remediation C): a document noun is
# NOT a document hint when it is the object of a filing verb ("file my
# FIR", "lodged a complaint", "draft a reply to the legal notice") — that
# is procedure, asked of the statute corpus, not a request to read an
# artifact. Up to two filler words and a second determiner/adjective are
# allowed between the verb and the noun. The negative lookahead excludes
# compound nouns where the -ing/-ed word is a MODIFIER, not a verb:
# "filing date", "filing fees", "notice period" are attributes of an
# artifact, not the act of filing one.
_FILING_VERB_RE = re.compile(
    rf"\b(?:fil\w*|lodg\w*|register\w*|submi\w*|serv\w*|giv\w*|issu\w*|draft\w*|withdraw\w*)\s+"
    rf"(?!dates?|fees?|procedure|process|requirements?|format|rules|timeline|period"
    rf"|deadline|limitation)\s*"
    rf"(?:an?\s+|the\s+|my\s+|our\s+|this\s+|that\s+|his\s+|her\s+|their\s+|any\s+|another\s+|no\s+)?"
    rf"(?:\w+\s+){{0,2}}"
    rf"(?:an?\s+|the\s+|my\s+|our\s+|this\s+|that\s+|his\s+|her\s+|their\s+|any\s+|another\s+|no\s+)?"
    rf"(?:\w+\s+)?{_DOCUMENT_NOUN_RE}\b",
    re.IGNORECASE,
)

# ...nor when the query is about the artifact's administrative fate rather
# than its content ("the police lost my FIR") — remedies are statute law.
_DOCUMENT_FATE_RE = re.compile(
    rf"\b(?:lost|missing|misplaced|stolen|destroyed|damaged|torn|rejected|returned|withheld)\s+"
    rf"(?:the\s+|my\s+|our\s+|this\s+|that\s+|his\s+|her\s+|their\s+)?{_DOCUMENT_NOUN_RE}\b",
    re.IGNORECASE,
)

# Indic document nouns (multilingual support, D-077): "इस दस्तावेज़ का
# सारांश दें", "এই দলিল...", "இந்த ஆவணம்..." route to the document side
# even when query translation is unavailable. Fail-closed like the English
# hints: no session means no document evidence.
_INDIC_DOCUMENT_HINT_RE = re.compile(
    "दस्तावेज|नोटिस|দস্তাবেজ|দলিল|নোটিশ|દસ્તાવેજ|ஆவண|நோட்டீஸ|పత్రం|ದಾಖಲೆ|രേഖ|ਦਸਤਾਵੇਜ਼|ਨੋਟਿਸ|ଦଲିଲ|ନୋଟିସ"
)

# Indic "section" words (D-077): "धारा 103", "ধারা ১০৩", "பிரிவு 103" take
# the deterministic section-lookup path when present in the original
# (untranslated) query. Devanagari/Bengali digits are normalized first.
_INDIC_SECTION_WORDS = (
    "धारा|कलम|ধারা|কলম|કલમ|பிரிவு|సెక్షన్|విభాగ|ವಿಧಿ|ಸೆಕ್ಷನ್|വകുപ്പ്|ਧਾਰਾ|ਸੈਕਸ਼ਨ|ଧାରା|ଅନୁଚ୍ଛେଦ|दफ़ा|दफा"
)
_INDIC_SECTION_RE = re.compile(
    rf"(?:{_INDIC_SECTION_WORDS})\s*(\d{{1,3}})(?:\s*\(\s*(\d{{1,3}})\s*\))?"
)

# Indic digit forms (all supported scripts, D-077) normalized to ASCII:
# Devanagari, Bengali/Assamese, Gujarati, and Odia blocks. Tamil, Telugu,
# Kannada, Malayalam, and Gurmukhi texts use ASCII digits in practice.
_DIGIT_TRANSLATION = {
    **{0x0966 + offset: str(offset) for offset in range(10)},
    **{0x09E6 + offset: str(offset) for offset in range(10)},
    **{0x0AE6 + offset: str(offset) for offset in range(10)},
    **{0x0B66 + offset: str(offset) for offset in range(10)},
}


def detect_section_intent(query: str) -> SectionIntent | None:
    """Return the direct-lookup intent for section-number queries.

    Supports "section 103", "section 103(2)", "BNS 103", "103 BNS", a
    bare number in a question ("What does 103 say?") when the number is not
    a quantity ("30 days") or a non-statute identifier ("case no. 5"), and
    the Indic section-word forms ("धारा 103", "ধারা ১০৩", D-077) so a
    non-English query still reaches the deterministic lookup without
    waiting for translation. Returns None when no section identifier is
    present.

    Indic digit forms are normalized to ASCII FIRST, in every path: a
    bare-number match on "१०३" previously returned the untranslated
    Devanagari digits and the store lookup silently missed.
    """
    query = query.translate(_DIGIT_TRANSLATION)
    indic_match = _INDIC_SECTION_RE.search(query)
    if indic_match:
        act = None
        act_mention = _ACT_MENTION_RE.search(query)
        if act_mention:
            act = act_mention.group(1).upper()
        return SectionIntent(
            act_short=act,
            section_number=indic_match.group(1),
            subsection=indic_match.group(2),
        )
    for pattern in (_SECTION_RE, _ACT_NUMBER_RE, _NUMBER_ACT_RE):
        match = pattern.search(query)
        if match:
            # "s. 7.5 lakh": the explicit prefix is part of a decimal
            # amount, not a section marker.
            if _DECIMAL_TAIL_RE.match(query[match.end() :]):
                continue
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

    # Bare number: only treat as a section reference when it is not a
    # quantity ("30 days") and not a non-statute identifier ("page 12").
    for match in _BARE_NUMBER_RE.finditer(query):
        tail = query[match.end() :]
        if _UNIT_FOLLOWER_RE.match(tail):
            continue
        if _DECIMAL_TAIL_RE.match(tail):
            continue
        # Indian comma grouping ("2,00,000"): a group that continues with
        # ",<digits>" or follows "<digits>," is part of a larger number,
        # not a section.
        if re.match(r"^\s*,\d", tail):
            continue
        prefix = query[: match.start()]
        if re.search(r"\d,\s*$", prefix):
            continue
        if _NON_STATUTE_PRECEDER_RE.search(prefix):
            continue
        if _DECIMAL_PREFIX_RE.search(prefix):
            continue
        act = None
        act_mention = _ACT_MENTION_RE.search(query)
        if act_mention:
            act = act_mention.group(1).upper()
        return SectionIntent(
            act_short=act,
            section_number=match.group(1),
            subsection=match.group(2),
        )
    return None


def _is_document_hint(query: str) -> bool:
    """True when the query asks about the user's own document artifact.

    A determiner + document-noun phrase is a hint unless the same noun is
    consumed by a procedural pattern (filing verb or administrative fate):
    the hint match must not overlap those spans.
    """
    hint = _DOCUMENT_HINT_RE.search(query)
    if hint is None:
        return False
    for procedural in (_FILING_VERB_RE, _DOCUMENT_FATE_RE):
        for match in procedural.finditer(query):
            if match.start() < hint.end() and hint.start() < match.end():
                return False
    return True


def classify_route(query: str) -> RetrievalRoute:
    """Statute / document / combined routing (ARCHITECTURE §14).

    Document hints are matched in English and in the supported Indic
    languages (D-077); English hints are suppressed when the document noun
    is used procedurally (filing an FIR, giving notice) rather than as a
    reference to an artifact whose content is being asked about.
    """
    document = _is_document_hint(query) or bool(_INDIC_DOCUMENT_HINT_RE.search(query))
    statute = bool(_ACT_MENTION_RE.search(query)) or detect_section_intent(query) is not None
    if document and statute:
        return RetrievalRoute.COMBINED
    if document:
        return RetrievalRoute.DOCUMENT
    return RetrievalRoute.STATUTE
