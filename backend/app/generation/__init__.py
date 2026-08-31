"""Grounded generation package (Phase 4: REQUIREMENTS A4-*, D-005..D-007).

Components:
* :mod:`app.generation.prompt` — grounding prompt contract (§17).
* :mod:`app.generation.citation_guard` — code-level citation validation (§18-§19).
* :mod:`app.generation.service` — confidence gate, refusal, generation, guard.
"""

from app.generation.citation_guard import (
    Citation,
    CitationCheck,
    build_sources,
    extract_citations,
    validate_citations,
)
from app.generation.service import REFUSAL_RESPONSE, GenerationOutcome, GenerationService

__all__ = [
    "REFUSAL_RESPONSE",
    "Citation",
    "CitationCheck",
    "GenerationOutcome",
    "GenerationService",
    "build_sources",
    "extract_citations",
    "validate_citations",
]
