"""Deterministic form output naming (REQUIREMENTS B-011..B-015; ARCHITECTURE §27).

``FORM-<number>_<slugified-title>.pdf`` where the slug is deterministic,
filesystem-safe, space-free and collision-resistant.
"""

from __future__ import annotations

import re

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9-]+")
_REPEAT_DASH_RE = re.compile(r"-{2,}")
_TRAILING_DASH_RE = re.compile(r"(^-+|-+$)")
_MAX_SLUG_CHARS = 80

# Connective words stay lowercase (ARCHITECTURE §27 example:
# "Bond-and-Bail-Bond-for-Attendance-before-Court").
_LOWERCASE_WORDS = {"and", "or", "for", "to", "of", "the", "a", "an", "in", "on", "by", "with"}


def slugify_title(title: str) -> str:
    """Convert a printed form title into the naming-convention slug.

    Gazette titles are printed in all caps with broken spacing; the required
    example output is Title Case joined by hyphens::

        "BOND AND BAIL-BOND AFTER ARREST UNDER A WARRANT"
        -> "Bond-and-Bail-Bond-After-Arrest-Under-a-Warrant"
    """
    words: list[str] = []
    for word in title.split():
        cleaned = _SAFE_CHARS_RE.sub("-", word)
        cleaned = _REPEAT_DASH_RE.sub("-", cleaned).strip("-")
        if not cleaned:
            continue
        # Capitalise each hyphen sub-word, connectives stay lowercase.
        parts = [
            part.lower() if part.lower() in _LOWERCASE_WORDS else part.capitalize()
            for part in cleaned.split("-")
        ]
        words.append("-".join(parts))
    slug = "-".join(words)[:_MAX_SLUG_CHARS]
    slug = _TRAILING_DASH_RE.sub("", slug)
    return slug or "Untitled"


def form_filename(form_number: int, title: str) -> str:
    """Build ``FORM-<number>_<slug>.pdf`` (B-011..B-014)."""
    return f"FORM-{form_number}_{slugify_title(title)}.pdf"


def ensure_unique(filename: str, taken: set[str]) -> str:
    """Disambiguate a filename deterministically when collisions occur (B-015)."""
    if filename not in taken:
        return filename
    stem, _, extension = filename.rpartition(".")
    counter = 2
    while f"{stem}-{counter}.{extension}" in taken:
        counter += 1
    return f"{stem}-{counter}.{extension}"
