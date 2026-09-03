"""Retrieval service (REQUIREMENTS A3-*; ARCHITECTURE §11-§15, §16 rerank deferral).

Independent of the HTTP layer: accepts a query plus routing/filter
information and returns structured evidence (``RetrievedEvidence``).

Pipeline per statute query::

    section intent? ──yes──▶ deterministic lookup (D-017)
          │ no
          ▼
    dense top-k + sparse top-k ─▶ RRF fusion (D-014) ─▶ evidence

Confidence evaluation (ARCHITECTURE §15) is measured and tunable — the
configured threshold is an initial value, not a hidden quality claim.
Cross-encoder reranking is deliberately deferred (D-016).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.retrieval.dense import DenseRetriever
from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import (
    MetadataFilter,
    RetrievalRoute,
    RetrievedEvidence,
    ScoredChunk,
    SectionIntent,
)
from app.retrieval.rrf import rrf_fuse
from app.retrieval.sparse import SparseRetriever
from app.retrieval.store import ChunkStore

if TYPE_CHECKING:
    from app.documents.retrieval import DocumentRetrievalService

logger = logging.getLogger(__name__)

# Semantic-relevance confidence gate (remediation of the RRF-overlap-only
# confidence signal). Measured on the BNS corpus with BAAI/bge-base-en-v1.5:
# on-target indirect questions score >= 0.50 top cosine ("Someone stole my
# scooter. What does BNS say?" 0.504 — the hardest observed in-scope case;
# typical conduct questions 0.6-0.72), while clearly out-of-scope or
# non-legal questions score <= 0.47 ("What is the rate of GST on restaurant
# bills?" 0.469; "capital of France" 0.347; injection payloads ~0.49* — see
# below). The floor/saturation band maps top cosine onto a [0, 1] relevance
# factor multiplied into the RRF confidence: high confidence then requires
# BOTH list overlap AND semantic relevance. The threshold (0.1) is
# unchanged — the signal became honest, not stricter on paper.
#
# The narrow gray zone above the floor (e.g. "What is Newton's second law
# of motion?" 0.547, whose "law/force/motion" vocabulary resembles BNS
# criminal-force text) is NOT resolvable by retrieval signals: it is
# delegated to the generation contract — the system prompt's rule 4 makes
# the model answer "I don't know based on the available source material."
# when the evidence does not contain the answer, which is the assignment's
# intended fail-closed behavior (A4-012) for subtly out-of-scope questions.
RELEVANCE_FLOOR = 0.48
RELEVANCE_SATURATION = 0.60

# Out-of-scope detection (SRC-013: corpus-derived, no hardcoded statute
# lists). A query "names a statute" when a statute keyword ("act", "code",
# "constitution", "amendment", "law") is surrounded by an adjacent run of
# name-like words — e.g. "Hindu Marriage Act", "Code of Civil Procedure",
# "Rajasthan Rent Control Act" — that shares NO content word with the
# indexed acts' names or short codes. The gate therefore generalizes to
# ANY un-indexed statute without enumerating them, and in-scope mentions
# ("the Bharatiya Nyaya Sanhita Act", "the Test Sanhita Act", "under this
# act") never match because they overlap the corpus act vocabulary or
# carry no adjacent name words at all.
_STATUTE_KEYWORDS = ("act", "code", "constitution", "amendment", "law")
# Words allowed INSIDE a name run ("Code of Civil Procedure") but never
# able to start or extend one on their own.
_NAME_INTERIOR_WORDS = {"of", "and"}
# A word that can appear inside the corpus's own nationality reference
# ("Indian law") without itself counting as statute-name content: the
# indexed statutes are Indian law, so a bare nationality mention is in
# scope, while "Indian Penal Code" still has the content word "penal"
# and is correctly out of scope.
_CORPUS_NATIONALITY_WORDS = {"india", "indian"}
# Words that break an adjacent name run: ordinary query vocabulary. A run
# must be a contiguous name-like span hugging the keyword, so "the act of
# murder" (no adjacent name words) and "punishment for murder under this
# act" (run broken by "this") are never treated as statute names.
_NAME_BREAK_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "said", "it", "its",
    "what", "which", "who", "whom", "whose", "how", "when", "where", "why",
    "does", "do", "did", "is", "are", "was", "were", "can", "could", "would",
    "should", "shall", "may", "might", "must", "will", "in", "on", "at", "to",
    "for", "from", "by", "with", "under", "over", "about", "against", "into",
    "my", "your", "our", "their", "his", "her", "say", "says", "tell", "ask",
    "explain", "describe", "me", "please", "section", "sections",
}
_NAME_RUN_WINDOW = 4  # max words of name on either side of the keyword

# Foreign jurisdictions ("punishment for murder in New York") need a seed
# list: the corpus metadata cannot say where a place is, so no corpus-
# derived signal exists for geography. The list is deployment config
# (Settings.retrieval_foreign_jurisdictions), not a code-time truth.
_DEFAULT_FOREIGN_JURISDICTIONS = (
    "new york", "california", "texas", "florida", "illinois", "ohio",
    "washington dc", "chicago", "los angeles", "boston", "seattle",
    "united states", "united kingdom", "england", "scotland", "wales",
    "ireland", "london", "canada", "australia", "pakistan", "bangladesh",
    "china", "japan", "singapore", "dubai", "united arab emirates",
    "germany", "france", "netherlands", "russia", "malaysia", "usa", "uk",
)


def _name_run(tokens: list[str], index: int, *, backwards: bool) -> list[str]:
    """Contiguous name-like words hugging the statute keyword at ``index``.

    Reading order. Stops at ordinary query vocabulary (``_NAME_BREAK_WORDS``)
    and at non-alphabetic tokens (numbers, parentheses), so only a genuine
    name span ("Hindu Marriage", "of Civil Procedure") is collected.
    """
    run: list[str] = []
    step = -1 if backwards else 1
    pos = index + step
    while len(run) < _NAME_RUN_WINDOW and 0 <= pos < len(tokens):
        token = tokens[pos].strip("\"'.,;:!?)(")
        if not token.isalpha() or token.lower() in _NAME_BREAK_WORDS:
            break
        run.append(token)
        pos += step
    if backwards:
        run.reverse()
    return run


class RetrievalService:
    """Query → structured evidence, statute corpus only at this phase."""

    def __init__(
        self,
        store: ChunkStore,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        *,
        dense_top_k: int = 20,
        sparse_top_k: int = 20,
        rrf_k: int = 60,
        confidence_threshold: float = 0.1,
        final_top_k: int = 10,
        document_confidence_threshold: float = 0.05,
        # Statute confidence below which a session's documents are also
        # consulted (§14 remediation, weak-evidence arm): the keyword router
        # cannot enumerate every way a user references their upload, so a
        # statute-routed question whose best BNS chunk is only marginally
        # relevant (relevance factor under this floor) still gets the
        # session's documents merged into the evidence rather than a
        # refusal over irrelevant statute text.
        document_fallback_confidence: float = 0.35,
        document_retrieval: DocumentRetrievalService | None = None,
        # Semantic-relevance gate band. The defaults are calibrated for
        # BAAI/bge-base-en-v1.5 cosine scores (see RELEVANCE_FLOOR). An
        # embedder with a different cosine scale (e.g. the deterministic
        # HashingEmbedder, where an on-target hit scores ~0.3) must NOT
        # reuse them: pass ``relevance_floor=None`` to disable the gate
        # and fall back to the RRF-overlap confidence signal alone.
        relevance_floor: float | None = RELEVANCE_FLOOR,
        relevance_saturation: float | None = RELEVANCE_SATURATION,
        # Foreign-jurisdiction seed list (deployment config; None keeps
        # the built-in default). See _DEFAULT_FOREIGN_JURISDICTIONS.
        foreign_jurisdictions: Sequence[str] | None = None,
    ) -> None:
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._dense_top_k = dense_top_k
        self._sparse_top_k = sparse_top_k
        self._rrf_k = rrf_k
        self._confidence_threshold = confidence_threshold
        self._document_fallback_confidence = document_fallback_confidence
        self._final_top_k = final_top_k
        self._document_confidence_threshold = document_confidence_threshold
        self._document_retrieval = document_retrieval
        self._relevance_floor = relevance_floor
        self._relevance_saturation = relevance_saturation
        jurisdictions = tuple(
            place.strip().lower()
            for place in (foreign_jurisdictions or _DEFAULT_FOREIGN_JURISDICTIONS)
            if place.strip()
        )
        self._foreign_jurisdiction_re = (
            re.compile(
                r"\b(?:" + "|".join(re.escape(place) for place in jurisdictions) + r")\b",
                re.IGNORECASE,
            )
            if jurisdictions
            else None
        )

    def retrieve(
        self,
        query: str,
        flt: MetadataFilter | None = None,
        *,
        route: RetrievalRoute | None = None,
        session_id: str | None = None,
        document_context: list[str] | None = None,
    ) -> RetrievedEvidence:
        """Run the retrieval pipeline for a query.

        ``route`` overrides intent-based routing when the caller (API
        layer) already knows the route. ``session_id`` scopes document
        retrieval (§21); without it document routes fail closed.
        ``document_context`` carries the documents cited in the recent
        conversation so follow-up references ("that document", "the other
        document") resolve deterministically.
        """
        from app.observability.metrics import RETRIEVAL_LATENCY

        with RETRIEVAL_LATENCY.observe_duration(route="total"):
            return self._retrieve(
                query, flt, route=route, session_id=session_id, document_context=document_context
            )

    def _retrieve(
        self,
        query: str,
        flt: MetadataFilter | None,
        route: RetrievalRoute | None,
        session_id: str | None,
        document_context: list[str] | None = None,
    ) -> RetrievedEvidence:
        resolved_route = route or classify_route(query)
        intent = detect_section_intent(query)
        reasons: list[str] = []

        if resolved_route == RetrievalRoute.DOCUMENT:
            return self._document_evidence(
                query, resolved_route, intent, session_id, reasons, document_context
            )

        if resolved_route == RetrievalRoute.COMBINED:
            document_evidence = self._document_evidence(
                query, resolved_route, intent, session_id, reasons, document_context
            )
            statute_evidence = self._statute_evidence(query, resolved_route, intent, flt, reasons)
            statute_evidence.document_hits = document_evidence.document_hits
            statute_evidence.sufficient = (
                statute_evidence.sufficient or document_evidence.sufficient
            )
            if document_evidence.sufficient:
                statute_evidence.reasons.append("session document evidence retrieved")
            return statute_evidence

        evidence = self._statute_evidence(query, resolved_route, intent, flt, reasons)
        # Document fallback (ARCHITECTURE §14 remediation): a statute-routed
        # query whose statute evidence is INSUFFICIENT falls through to the
        # session's documents before failing closed. The keyword router
        # cannot enumerate every way a user references their upload ("What
        # is the filing date?", "What did the petitioner seek?"), so the
        # route hint is advisory: when the statute corpus cannot ground the
        # question but the session's own documents can, the documents are
        # the evidence. A session without documents, or document retrieval
        # below its confidence threshold, still refuses honestly — the
        # fallback only rescues document-groundable questions, never
        # substitutes weak statute evidence for a refusal.
        #
        # Weak-evidence arm (2026-09-03): statute evidence can be formally
        # sufficient (above the 0.1 gate) while its best chunk is only
        # marginally relevant — the question really targets an uploaded
        # document the router did not recognize ("How much rent arrears
        # does the tenant owe?"). Below the fallback confidence the session
        # documents are MERGED into the evidence (statute chunks stay), so
        # the model can cite whichever corpus actually answers; a refusal
        # over irrelevant statute text is never forced when the documents
        # can ground the question.
        weak_statute = evidence.confidence < self._document_fallback_confidence
        if (
            (not evidence.sufficient or weak_statute)
            and session_id is not None
            and self._document_retrieval is not None
        ):
            document_evidence = self._document_evidence(
                query, resolved_route, intent, session_id, reasons, document_context
            )
            if document_evidence.sufficient:
                if not evidence.sufficient:
                    document_evidence.route = resolved_route
                    document_evidence.reasons.append(
                        "statute evidence insufficient; session documents retrieved"
                    )
                    return document_evidence
                if evidence.results:
                    evidence.document_hits = document_evidence.document_hits
                    evidence.sufficient = True
                    evidence.reasons.append(
                        "session document evidence merged with weak statute evidence"
                    )
                else:
                    document_evidence.route = resolved_route
                    document_evidence.reasons.append(
                        "statute evidence empty; session documents retrieved"
                    )
                    return document_evidence
        return evidence

    def _document_evidence(
        self,
        query: str,
        route: RetrievalRoute,
        intent: SectionIntent | None,
        session_id: str | None,
        reasons: list[str],
        document_context: list[str] | None = None,
    ) -> RetrievedEvidence:
        """Session-scoped user-document retrieval (§21, §34).

        Isolation fails closed: no session id or no configured document
        index means no document evidence — never a global search.
        """
        if session_id is None:
            reasons.append("document route requested without a session id")
            return RetrievedEvidence(
                query=query,
                route=route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
            )
        if self._document_retrieval is None:
            reasons.append("document retrieval is not configured")
            return RetrievedEvidence(
                query=query,
                route=route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
            )
        evidence = self._document_retrieval.retrieve(
            session_id, query, context_document_ids=document_context
        )
        reasons.extend(evidence.reasons)
        confidence = evidence.hits[0].score if evidence.hits else 0.0
        sufficient = bool(evidence.hits) and (
            # An explicitly referenced document ("the first document") is
            # grounded by identity: a weak content match must not refuse a
            # question the user asked ABOUT that document.
            evidence.reference_anchored
            or confidence >= self._document_confidence_threshold
        )
        retrieved = RetrievedEvidence(
            query=query,
            route=route,
            intent=intent,
            results=[],
            document_hits=evidence.hits,
            sufficient=sufficient,
            confidence=confidence,
            reasons=reasons,
        )
        if not evidence.hits:
            retrieved.reasons.append("no session document chunks matched")
        elif not sufficient:
            retrieved.reasons.append(
                f"document retrieval confidence {confidence:.3f} below threshold "
                f"{self._document_confidence_threshold:.3f}"
            )
        self._log(query, retrieved)
        return retrieved

    def _foreign_statute(self, query: str) -> str | None:
        """Name of a statute the query asks about that is not the corpus.

        Corpus-derived (SRC-013): NO hardcoded list of statute names. A
        statute keyword ("act", "code", ...) with an adjacent run of
        name-like words names a statute; the run is checked against the
        indexed acts' vocabulary (act names and short codes from chunk
        metadata). A mention that shares a content word with the corpus
        ("Bharatiya Nyaya Sanhita Act", "the BNSS Act", "under this act")
        is in scope; any OTHER statute name — whether a famous one
        ("Hindu Marriage Act") or an obscure one ("Rajasthan Rent Control
        Act") — is out of scope, because the corpus cannot ground it and
        retrieval must fail closed rather than substitute look-alike
        sections (A4-011). Document evidence is not affected: a user's
        own upload may legitimately cite other statutes.
        """
        if self._foreign_jurisdiction_re is not None:
            jurisdiction = self._foreign_jurisdiction_re.search(query)
            if jurisdiction is not None:
                return f"{jurisdiction.group(0)} law"
        return self._statute_name_mention(query)

    def _corpus_vocabulary(self) -> set[str]:
        """Content-word vocabulary of the indexed acts (names + shorts)."""
        words: set[str] = set()
        for act in self._store.act_names():
            words.update(re.findall(r"[a-z]+", act.lower()))
        words.update(short.lower() for short in self._store.act_shorts())
        return words - {"act", "code", "2023"} | _CORPUS_NATIONALITY_WORDS

    def _statute_name_mention(self, query: str) -> str | None:
        """Adjacent-run statute-name detection, or None when in scope.

        A name run is a contiguous span of at most ``_NAME_RUN_WINDOW``
        alphabetic words hugging a statute keyword, optionally containing
        interior connectives ("of", "and"). The mention counts only when
        it carries >= 2 content words, or a single Capitalized one
        ("the Penal Code"), and none of its content words (minus the
        corpus nationality markers) appear in the corpus vocabulary.
        """
        tokens = query.replace("’", "'").split()
        corpus_words = self._corpus_vocabulary()
        for index, token in enumerate(tokens):
            core = token.rstrip("\"'.,;:!?")
            if not core.isalpha():
                continue  # "Act's", "Acts," or quoted/hyphenated — not the bare keyword
            word = core.lower()
            if word not in _STATUTE_KEYWORDS:
                continue
            before = _name_run(tokens, index, backwards=True)
            # Suffix-form names ("Hindu Marriage Act") put the name before
            # the keyword; prefix-form names ("Code of Civil Procedure")
            # are the only ones that put it after — and they always open
            # with a connective. Requiring that stops ordinary predicates
            # ("law punish theft") from being read as a name.
            after: list[str] = []
            if index + 1 < len(tokens):
                nxt = tokens[index + 1].strip("\"'.,;:!?)(")
                if nxt.lower() in _NAME_INTERIOR_WORDS:
                    after = _name_run(tokens, index, backwards=False)
            content = [w for w in (*before, *after) if w.lower() not in _NAME_INTERIOR_WORDS]
            if not content:
                continue
            if len(content) == 1 and not content[0][:1].isupper():
                # A lone lowercase word ("the act of murder") is not a name.
                continue
            non_nationality = [
                w for w in content if w.lower() not in _CORPUS_NATIONALITY_WORDS
            ]
            if not non_nationality:
                # Bare nationality ("under Indian law") — the corpus IS
                # Indian law, so the mention is in scope.
                continue
            if any(w.lower() in corpus_words for w in non_nationality):
                continue  # Shares vocabulary with an indexed act: in scope.
            name = " ".join((*before, core, *after)).strip()
            return name or core
        return None

    def _relevance_factor(self, query: str, flt: MetadataFilter | None) -> float | None:
        """Semantic relevance in [0, 1], or None when unavailable.

        Unavailable means the dense retriever exposes no similarity signal
        (test doubles, degraded wiring) or the gate is disabled for this
        embedder's cosine scale (``relevance_floor=None``): the confidence
        then falls back to the RRF-overlap signal alone.
        """
        if self._relevance_floor is None or self._relevance_saturation is None:
            return None
        top_similarity = getattr(self._dense, "top_similarity", None)
        if top_similarity is None:
            return None
        raw = top_similarity(query, flt)
        if raw is None:
            return None
        cosine = float(raw)
        if cosine <= self._relevance_floor:
            return 0.0
        if cosine >= self._relevance_saturation:
            return 1.0
        return (cosine - self._relevance_floor) / (
            self._relevance_saturation - self._relevance_floor
        )

    def _statute_evidence(
        self,
        query: str,
        route: RetrievalRoute,
        intent: SectionIntent | None,
        flt: MetadataFilter | None,
        reasons: list[str],
    ) -> RetrievedEvidence:
        resolved_route = route
        if resolved_route == RetrievalRoute.DOCUMENT:  # pragma: no cover - defensive
            resolved_route = RetrievalRoute.STATUTE

        foreign = self._foreign_statute(query)
        if foreign is not None:
            # The question is about a statute the corpus cannot ground:
            # fail closed (A4-011) instead of substituting look-alike
            # sections from the indexed act. The evidence carries the
            # indexed act names so the refusal can name the corpus that
            # failed to ground the question.
            reasons.append(f"query names statute '{foreign}' which is not the indexed corpus")
            evidence = RetrievedEvidence(
                query=query,
                route=resolved_route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
                indexed_acts=sorted(self._store.act_names()),
            )
            self._log(query, evidence)
            return evidence

        if intent is not None:
            # Deterministic precedence for exact identifiers (A3-014).
            chunks = self._store.section_lookup(intent.section_number, act_short=intent.act_short)
            if not chunks and intent.act_short is not None:
                # The user's act label ("BNS") may not match the indexed
                # corpus act_short. Retrying without the act restriction is
                # only safe while the corpus holds a single act — then the
                # label is an alias, not a different authority. With a
                # multi-act corpus the requested act is genuinely absent,
                # so we refuse rather than substitute the wrong act's text.
                if len(self._store.act_shorts()) <= 1:
                    chunks = self._store.section_lookup(intent.section_number)
                else:
                    reasons.append(f"act {intent.act_short} not present in the indexed corpus")
            if flt is not None:
                chunks = [c for c in chunks if self._store.matches(c, flt)]
            if intent.subsection is not None:
                # A lookup naming a subsection ("103(2)") prefers the
                # chunks for that exact subsection; the whole-section
                # chunks are the fallback when the corpus splits the
                # section differently.
                chunks = self._narrow_subsection(chunks, intent.subsection) or chunks
            lookup_results = [
                ScoredChunk(chunk=chunk, source="lookup", rrf_score=1.0) for chunk in chunks
            ]
            evidence = RetrievedEvidence(
                query=query,
                route=resolved_route,
                intent=intent,
                results=lookup_results,
                sufficient=bool(lookup_results),
                confidence=1.0 if lookup_results else 0.0,
                reasons=reasons,
            )
            if not lookup_results:
                evidence.reasons.append(
                    f"section {intent.section_number} not present in indexed corpus"
                )
            self._log(query, evidence)
            return evidence

        # Hybrid: dense + sparse candidate pools, RRF fusion (D-014/D-015).
        dense_ids = self._dense.search(query, flt, self._dense_top_k)
        sparse_ids = self._sparse.search(query, flt, self._sparse_top_k)
        fused = rrf_fuse(dense_ids, sparse_ids, k=self._rrf_k)[: self._final_top_k]
        dense_rank = {cid: rank for rank, cid in enumerate(dense_ids, start=1)}
        sparse_rank = {cid: rank for rank, cid in enumerate(sparse_ids, start=1)}

        results: list[ScoredChunk] = []
        for chunk_id, score in fused:
            chunk = self._store.get(chunk_id)
            if chunk is None:
                continue
            if not self._store.matches(chunk, flt):
                continue  # filter enforced server-side for any retriever
            if dense_rank.get(chunk_id) and sparse_rank.get(chunk_id):
                source = "hybrid"
            elif dense_rank.get(chunk_id):
                source = "dense"
            else:
                source = "sparse"
            results.append(
                ScoredChunk(
                    chunk=chunk,
                    rrf_score=score,
                    dense_rank=dense_rank.get(chunk_id),
                    sparse_rank=sparse_rank.get(chunk_id),
                    source=source,
                )
            )

        confidence = self._confidence(results)
        relevance = self._relevance_factor(query, flt)
        if relevance is not None:
            # RRF overlap alone reports ~1.0 for confidently irrelevant
            # evidence; the semantic factor makes high confidence mean
            # relevance (remediation of the blind confidence signal).
            confidence = confidence * relevance
            reasons.append(f"semantic relevance factor {relevance:.3f} from top dense similarity")
        reasons.append(
            f"retrieved {len(results)} chunk(s) via "
            f"dense({len(dense_ids)}) + sparse({len(sparse_ids)})"
        )
        sufficient = bool(results) and confidence >= self._confidence_threshold
        if results and not sufficient:
            reasons.append(
                f"retrieval confidence {confidence:.3f} below threshold "
                f"{self._confidence_threshold:.3f}"
            )
        if not results:
            reasons.append("no chunks matched the query in the indexed corpus")

        evidence = RetrievedEvidence(
            query=query,
            route=resolved_route,
            intent=None,
            results=results,
            sufficient=sufficient,
            confidence=confidence,
            reasons=reasons,
        )
        self._log(query, evidence)
        return evidence

    @staticmethod
    def _narrow_subsection(chunks: list, subsection: str) -> list:
        """Chunks for the exact subsection of a section lookup, may be [].

        A chunk matches when its own subsection metadata equals the wanted
        one, or when a whole-section chunk's text carries the inline
        "(n)" marker. Chunks for a different subsection never match.
        """
        wanted = subsection.strip("()")
        specific = []
        for chunk in chunks:
            same_sub = chunk.subsection and chunk.subsection.strip("()") == wanted
            inline_marker = chunk.subsection is None and f"({wanted})" in chunk.text
            if same_sub or inline_marker:
                specific.append(chunk)
        return specific

    def _confidence(self, results: list[ScoredChunk]) -> float:
        """Normalized RRF confidence: top score / theoretical max.

        The theoretical max is the score of a result ranked first in both
        lists: 2/(k+1). Normalizing puts overlap-confirmed results near
        1.0 — measurable and tunable, per ARCHITECTURE §15.
        """
        if not results:
            return 0.0
        max_score = 2.0 / (self._rrf_k + 1)
        return min(results[0].rrf_score / max_score, 1.0)

    def _log(self, query: str, evidence: RetrievedEvidence) -> None:
        logger.info(
            "retrieval complete",
            extra={
                "event": "retrieval_complete",
                "route": evidence.route.value,
                "intent": evidence.intent.section_number if evidence.intent else None,
                "results": len(evidence.results),
                "document_hits": len(evidence.document_hits),
                "confidence": round(evidence.confidence, 4),
                "sufficient": evidence.sufficient,
                "query_length": len(query),
            },
        )
