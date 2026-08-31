"""Deterministic conversational short-circuit (DECISIONS D-067/D-068).

A small, explainable pre-retrieval layer for clearly conversational
messages ("hi", "thanks", "who are you", "what can you do"). Matching is
exact-phrase or full-message pattern based over normalized text: anything
that is not a verbatim social formula or a whole-message identity /
capability question — including every legal question, ambiguous message,
and prompt-injection payload — falls through to the full RAG pipeline
unchanged (§32.1).

Replies are fixed strings produced by code, never by the model. They make
no legal claim, cite nothing, and assert no runtime state (no "I searched",
no "I checked your document"): identity and capability replies describe
only what the product is by construction, which is true whether or not the
model or corpus is currently up.
"""

from __future__ import annotations

import re

# Replies by intent category. Copy is state-free by construction.
_GREETING_REPLY = (
    "Hello! I'm Nyaya. Ask me a question about the law, "
    "or upload a legal document to ask questions about it."
)
_THANKS_REPLY = "You're welcome! Ask me another question about the law any time."
_FAREWELL_REPLY = "Goodbye! Come back any time with a question about the law."
_ACK_REPLY = "Got it. Ask me a question about the law whenever you're ready."
_HOW_ARE_YOU_REPLY = "I'm here and ready. Ask me a question about the law any time."
_IDENTITY_REPLY = (
    "I'm Nyaya, an AI legal assistant for questions about Indian criminal law. "
    "I answer only from the legal source material I'm given, citing the exact "
    "sections I quote. I'm not a lawyer, and this is not legal advice."
)
_CAPABILITY_REPLY = (
    "I can answer questions about the law from the indexed source material, "
    "citing the exact sections I quote, and I can answer questions about a "
    "legal document you upload. If the material doesn't contain an answer, "
    "I say so. I'm not a lawyer, and this is not legal advice."
)

# Exact normalized phrases per category. Additions must remain single
# social formulas — never phrases that could carry a legal question.
_PHRASES: dict[str, frozenset[str]] = {
    "greeting": frozenset(
        {"hi", "hello", "hey", "namaste", "good morning", "good afternoon", "good evening"}
    ),
    "thanks": frozenset(
        {
            "thanks",
            "thank you",
            "thank you very much",
            "much appreciated",
            "many thanks",
            "thanks a lot",
            "thank you so much",
        }
    ),
    "farewell": frozenset(
        {"goodbye", "good bye", "bye", "bye bye", "see you", "see you later", "good night"}
    ),
    "ack": frozenset(
        {"ok", "okay", "alright", "sure", "got it", "cool", "nice", "great", "yes", "yeah", "no"}
    ),
}

_REPLIES: dict[str, str] = {
    "greeting": _GREETING_REPLY,
    "thanks": _THANKS_REPLY,
    "farewell": _FAREWELL_REPLY,
    "ack": _ACK_REPLY,
    "how_are_you": _HOW_ARE_YOU_REPLY,
    "identity": _IDENTITY_REPLY,
    "capability": _CAPABILITY_REPLY,
}

# Whole-message patterns for identity / capability / smalltalk questions.
# Anchored (^...$) so any additional content — a section number, a legal
# question, an injected instruction — disqualifies the message (D-067).
# Exception: a leading social formula ("hello, ...", "namaste, ...") is
# stripped before matching so the natural "hello, who are you?" is still
# intercepted as a whole conversational message; the prefix is itself
# conversational and can carry no legal content.
_TAIL = r"[\s?!.]*$"
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Identity.
    (re.compile(r"^who (?:are|r) (?:you|u)" + _TAIL), "identity"),
    (re.compile(r"^what (?:are|r) (?:you|u)" + _TAIL), "identity"),
    (re.compile(r"^who(?:'s| is| are) nyaya" + _TAIL), "identity"),
    (re.compile(r"^what(?:'s| is) nyaya" + _TAIL), "identity"),
    (re.compile(r"^what(?:'s| is) (?:your|ur) name" + _TAIL), "identity"),
    (re.compile(r"^what (?:are|r) (?:you|u) (?:called|named)" + _TAIL), "identity"),
    (re.compile(r"^introduce (?:yourself|u rself)" + _TAIL), "identity"),
    (re.compile(r"^tell me about (?:yourself|u rself)" + _TAIL), "identity"),
    (
        re.compile(
            r"^(?:are|am) (?:you|u) (?:an? )?"
            r"(?:bot|robot|ai|chatbot|assistant|human|lawyer|real|alive)" + _TAIL
        ),
        "identity",
    ),
    (re.compile(r"^what(?:'s| is) (?:your|ur) (?:purpose|role)" + _TAIL), "identity"),
    # Capability.
    (re.compile(r"^what (?:can|do) (?:you|u) do" + _TAIL), "capability"),
    (re.compile(r"^what (?:can|do) (?:you|u) know" + _TAIL), "capability"),
    (re.compile(r"^what(?:'s| is) (?:your|ur) (?:purpose|role|function)" + _TAIL), "capability"),
    (re.compile(r"^how (?:can|do|would) (?:you|u) help(?: (?:me|us))?" + _TAIL), "capability"),
    (re.compile(r"^what can (?:you|u) help (?:me|us) with" + _TAIL), "capability"),
    (re.compile(r"^can (?:you|u) help(?: (?:me|us))?" + _TAIL), "capability"),
    (re.compile(r"^what (?:are|r) (?:your|ur) (?:features|capabilities)" + _TAIL), "capability"),
    (re.compile(r"^help" + _TAIL), "capability"),
    # Smalltalk.
    (re.compile(r"^how (?:are|r) (?:you|u)" + _TAIL), "how_are_you"),
    (re.compile(r"^how(?:'s| is) it going" + _TAIL), "how_are_you"),
    (re.compile(r"^how (?:are|r) (?:you|u) doing" + _TAIL), "how_are_you"),
]

# Strip common sentence punctuation and collapse whitespace for matching.
_STRIP_CHARS = " \t\r\n.,!?;:'\"()-_"

_WHITESPACE_RE = re.compile(r"\s+")

# Leading social formulas stripped before whole-message pattern matching
# ("hello, who are you?" → "who are you?"). Same vocabulary as the
# greeting category — nothing that could carry a legal question.
_LEAD_GREETING_RE = re.compile(
    r"^(?:hello|hi|hey|namaste|good (?:morning|afternoon|evening))"
    r"[\s,.!?]*",
    re.IGNORECASE,
)


def _normalize(message: str) -> str:
    """Lowercase, collapse internal whitespace, trim edge punctuation."""
    return _WHITESPACE_RE.sub(" ", message.lower()).strip().strip(_STRIP_CHARS).strip()


def conversational_category(message: str) -> str | None:
    """Return the conversational category for a clearly conversational message.

    None means "not clearly conversational" — the caller must run the
    normal retrieval pipeline. Exact phrases match the normalized message
    verbatim; pattern categories must match the whole message, so any
    additional word (a question, a section reference, an injected
    instruction) disqualifies it.
    """
    normalized = _normalize(message)
    if not normalized:
        return None
    for category, phrases in _PHRASES.items():
        if normalized in phrases:
            return category
    for pattern, category in _PATTERNS:
        if pattern.match(normalized):
            return category
    stripped = _LEAD_GREETING_RE.sub("", normalized, count=1)
    if stripped and stripped != normalized:
        for pattern, category in _PATTERNS:
            if pattern.match(stripped):
                return category
    return None


def reply_for_category(category: str) -> str | None:
    """Fixed English reply for a category (None for unknown categories)."""
    return _REPLIES.get(category)


def conversational_reply(message: str) -> str | None:
    """Return the fixed English reply for a clearly conversational message."""
    category = conversational_category(message)
    if category is None:
        return None
    return _REPLIES[category]
