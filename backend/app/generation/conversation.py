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

Greeting variety: time-of-day greetings ("Good Morning", "Good Evening",
"Suprabhat", "Shubh Sandhya") map to their own reply buckets, and every
greeting bucket holds multiple code-authored variants. Variant selection is
deterministic per message (MD5 of the normalized message modulo the variant
count), so the same message always gets the same reply while different
messages spread across the variants. No randomness, no runtime state.
"""

from __future__ import annotations

import hashlib
import re

# --------------------------------------------------------------------------
# Replies by category. Copy is state-free by construction.
# Multiple variants per greeting bucket: deterministic pick per message
# (see _pick_variant). Every variant is capability-honest (D-067/D-068).
# --------------------------------------------------------------------------
_GREETING_REPLIES: tuple[str, ...] = (
    "Hello! I'm Nyaya. Ask me a question about the law, "
    "or upload a legal document to ask questions about it.",
    "Hello there! I'm Nyaya. Bring me a question about the law, "
    "or a legal document you want to ask about.",
    "Hi! I'm Nyaya. What law question can I help with today?",
    "Hey! I'm Nyaya. Ask me a question about the law, or upload a legal document to dig into.",
    "Greetings! I'm Nyaya. A law question or an uploaded document — either works.",
)
_MORNING_REPLIES: tuple[str, ...] = (
    "Good morning! I'm Nyaya. Ask me a question about the law, "
    "or upload a legal document to ask questions about it.",
    "Good morning! I'm Nyaya — ready for your law questions, or a document you want to ask about.",
    "Morning! I'm Nyaya. What law question is on your mind?",
)
_AFTERNOON_REPLIES: tuple[str, ...] = (
    "Good afternoon! I'm Nyaya. Ask me a question about the law, "
    "or upload a legal document to ask questions about it.",
    "Good afternoon! I'm Nyaya — what law question can I help with?",
    "Afternoon! I'm Nyaya. Bring a law question, or a document to ask about.",
)
_EVENING_REPLIES: tuple[str, ...] = (
    "Good evening! I'm Nyaya. Ask me a question about the law, "
    "or upload a legal document to ask questions about it.",
    "Good evening! I'm Nyaya — ready when you are, with a law question or a document.",
    "Evening! I'm Nyaya. What law question can I answer for you?",
)
_THANKS_REPLIES: tuple[str, ...] = (
    "You're welcome! Ask me another question about the law any time.",
    "Happy to help! Ask me another law question whenever you like.",
    "Any time! Bring me the next law question when you have it.",
)
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

# Categories shared with app.language.conversation (D-077). The greeting
# buckets extend the base "greeting" category; multilingual layers that do
# not know them fall back to the English variants.
GREETING_CATEGORIES = (
    "greeting",
    "greeting_morning",
    "greeting_afternoon",
    "greeting_evening",
)
CATEGORIES = (
    *GREETING_CATEGORIES,
    "thanks",
    "farewell",
    "ack",
    "how_are_you",
    "identity",
    "capability",
)

# Reply variants per category. Categories absent here have a single reply.
_VARIANTS: dict[str, tuple[str, ...]] = {
    "greeting": _GREETING_REPLIES,
    "greeting_morning": _MORNING_REPLIES,
    "greeting_afternoon": _AFTERNOON_REPLIES,
    "greeting_evening": _EVENING_REPLIES,
    "thanks": _THANKS_REPLIES,
}
_REPLIES: dict[str, str] = {
    "greeting": _GREETING_REPLIES[0],
    "greeting_morning": _MORNING_REPLIES[0],
    "greeting_afternoon": _AFTERNOON_REPLIES[0],
    "greeting_evening": _EVENING_REPLIES[0],
    "thanks": _THANKS_REPLIES[0],
    "farewell": _FAREWELL_REPLY,
    "ack": _ACK_REPLY,
    "how_are_you": _HOW_ARE_YOU_REPLY,
    "identity": _IDENTITY_REPLY,
    "capability": _CAPABILITY_REPLY,
}

# Exact normalized phrases per category. Additions must remain single
# social formulas — never phrases that could carry a legal question.
# Time-of-day greetings (English and romanized Hindi/Marathi) get their
# own buckets so the reply can mirror the time of day.
_PHRASES: dict[str, frozenset[str]] = {
    "greeting": frozenset({"hi", "hello", "hey", "namaste", "namaskar", "namaskaar", "namaskaram"}),
    "greeting_morning": frozenset({"good morning", "suprabhat", "shubh prabhat"}),
    "greeting_afternoon": frozenset({"good afternoon", "shubh dopahar"}),
    "greeting_evening": frozenset({"good evening", "shubh sandhya"}),
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
# ("hello, who are you?" → "who are you?"). Same vocabulary as the greeting
# buckets — nothing that could carry a legal question.
_LEAD_GREETING_RE = re.compile(
    r"^(?:hello|hi|hey|namaste|namaskar|namaskaar|namaskaram|suprabhat|shubh prabhat|"
    r"shubh dopahar|shubh sandhya|good (?:morning|afternoon|evening))"
    r"[\s,.!?]*",
    re.IGNORECASE,
)


def _normalize(message: str) -> str:
    """Lowercase, collapse internal whitespace, trim edge punctuation."""
    return _WHITESPACE_RE.sub(" ", message.lower()).strip().strip(_STRIP_CHARS).strip()


def _pick_variant(variants: tuple[str, ...], message: str) -> str:
    """Deterministic variant choice: stable per message, varied across messages.

    MD5 of the normalized message (process-stable, unlike ``hash``) modulo
    the variant count. The same message always yields the same reply —
    required for replayable tests — while different messages distribute
    over the variants.
    """
    digest = hashlib.md5(message.encode("utf-8")).digest()
    return variants[int.from_bytes(digest[:4], "big") % len(variants)]


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


def reply_for_category(category: str, message: str | None = None) -> str | None:
    """Fixed English reply for a category (None for unknown categories).

    ``message`` selects a deterministic variant for categories that hold
    several (the greeting and thanks buckets). Without ``message`` the
    first variant is returned — the stable default for callers that have
    no message at hand (e.g. the multilingual fallback path).
    """
    variants = _VARIANTS.get(category)
    if variants is not None and message is not None:
        return _pick_variant(variants, _normalize(message))
    return _REPLIES.get(category)


def conversational_reply(message: str) -> str | None:
    """Return the fixed English reply for a clearly conversational message."""
    category = conversational_category(message)
    if category is None:
        return None
    return reply_for_category(category, message)
