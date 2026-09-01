"""Multilingual statute-name aliases for query translation (D-094).

Translation is a lossy channel: a small local model asked to translate
"भारतीय न्याय संहिता की धारा 303" into English sometimes substitutes the
act it "knows" — "Indian Penal Code" — instead of the act the user named.
Downstream, the retrieval layer's foreign-statute guard (A4-011) then
correctly fails closed on a statute the user never asked about, and an
in-scope question is refused.

This module gives the translation seam a deterministic safety net: the
set of statutes a text mentions, detected across the supported Indic
scripts, so ``LanguageService.translate_query`` can verify the translation
mentions the same statutes as the original and repair it when the model
re-branded one.

This is translation fidelity data, NOT corpus knowledge: the retrieval
layer still decides scope from chunk metadata alone (SRC-013).
"""

from __future__ import annotations

import re

#: Canonical English name → aliases in every supported script (lowercased;
#: matched on the lowercased text, so Latin casing never matters). The
#: canonical names are the ones a correct English translation would use.
_ACT_ALIASES: dict[str, tuple[str, ...]] = {
    "Bharatiya Nyaya Sanhita": (
        "bharatiya nyaya sanhita",
        "bns",
        "भारतीय न्याय संहिता",
    ),
    "Bharatiya Nagarik Suraksha Sanhita": (
        "bharatiya nagarik suraksha sanhita",
        "bnss",
        "भारतीय नागरिक सुरक्षा संहिता",
    ),
    "Indian Penal Code": (
        "indian penal code",
        "ipc",
        "भारतीय दंड संहिता",
        "भारतीय दण्ड संहिता",
    ),
    "Code of Criminal Procedure": (
        "code of criminal procedure",
        "crpc",
        "दंड प्रक्रिया संहिता",
        "दण्ड प्रक्रिया संहिता",
    ),
}

#: Alias (lowercase) → canonical English name.
_ALIAS_TO_ACT: dict[str, str] = {
    alias: canonical for canonical, aliases in _ACT_ALIASES.items() for alias in aliases
}

_ALIAS_PATTERN = re.compile(
    "|".join(re.escape(alias) for alias in sorted(_ALIAS_TO_ACT, key=len, reverse=True))
)


def mentioned_acts(text: str) -> set[str]:
    """Canonical English names of every statute the text mentions.

    Latin aliases are matched word-boundary-aware via a small correction
    below ("bns"/"ipc"/"crpc" are tokens, not substrings); non-Latin
    aliases are long enough to match as-is.
    """
    lowered = text.lower()
    found = set()
    for match in _ALIAS_PATTERN.finditer(lowered):
        alias = match.group(0)
        if alias in ("bns", "bnss", "ipc", "crpc"):
            # Token match: reject "ipc" inside "ipcs" etc. Both neighbours
            # must be non-alphanumeric (start/end of string counts).
            start, end = match.start(), match.end()
            before = lowered[start - 1] if start > 0 else " "
            after = lowered[end] if end < len(lowered) else " "
            if before.isalnum() or after.isalnum():
                continue
        found.add(_ALIAS_TO_ACT[alias])
    return found


def replace_act_mentions(text: str, replacements: dict[str, str]) -> str:
    """Rewrite alias mentions of one statute into another statute's
    canonical English name.

    ``replacements`` maps the canonical name to REMOVE → the canonical
    name to insert. Only mentions of the removed statute (any alias) are
    rewritten; everything else in the text is left untouched.
    """
    for remove, insert in replacements.items():
        for alias in _ACT_ALIASES.get(remove, ()):
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            text = pattern.sub(insert, text)
    return text
