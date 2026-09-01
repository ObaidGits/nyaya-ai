"""Evaluation runner core (IMPLEMENTATION_PLAN §8.2, ARCHITECTURE §42).

Deterministic end-to-end evaluation over the golden set: builds the
retrieval pipeline in-process (HashingEmbedder — no network, no model
runtime) and scores retrieval, refusal and latency. Optional LLM mode
adds answer generation for citation accuracy and refusal rate; it is
explicitly flagged non-deterministic in the result metadata.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evaluation.golden import GoldenCase, load_golden_set
from app.evaluation.metrics import (
    citation_accuracy,
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
    refusal_correctness,
)
from app.generation.service import GenerationService
from app.ingestion.embeddings import HashingEmbedder
from app.retrieval.dense import CosineDenseIndex
from app.retrieval.models import RetrievedEvidence
from app.retrieval.service import RELEVANCE_FLOOR, RELEVANCE_SATURATION, RetrievalService
from app.retrieval.sparse import Bm25SparseIndex
from app.retrieval.store import ChunkStore

SESSION_ID = "eval-session"

# Fixture document content for document/combined cases. Synthetic text, not
# legal material — no fabricated law.
FIXTURE_DOCUMENT_PAGES = [
    "RENTAL AGREEMENT NOTICE. This document is a sample rental agreement notice "
    "created for evaluation. The tenant must provide 30 days written notice before "
    "vacating the premises. The security deposit must be refunded within 15 days.",
]


@dataclass
class CaseResult:
    """Scored outcome of one golden case under one configuration."""

    case_id: str
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    reciprocal_rank: float = 0.0
    refusal_correct: float = 0.0
    document_hit: bool = False
    citation_accuracy: float | None = None
    retrieval_latency: float = 0.0
    generation_latency: float | None = None
    error: str | None = None


@dataclass
class RunResult:
    """Aggregate metrics for one configuration."""

    config_name: str
    case_results: list[CaseResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        answered = [r for r in self.case_results if r.error is None]
        doc_cases = [r for r in answered if r.case_id.startswith(("doc-", "combined-"))]
        citation_scores = [r.citation_accuracy for r in answered if r.citation_accuracy is not None]
        return {
            "config": self.config_name,
            "cases": len(self.case_results),
            "failed_cases": len(self.failures),
            "failures": list(self.failures),
            "recall_at_5": mean([r.recall_at_5 for r in answered]),
            "recall_at_10": mean([r.recall_at_10 for r in answered]),
            "mrr": mean([r.reciprocal_rank for r in answered]),
            "citation_accuracy": mean(citation_scores) if citation_scores else None,
            "refusal_correctness": mean([r.refusal_correct for r in answered]),
            "document_hit_rate": (
                mean([1.0 if r.document_hit else 0.0 for r in doc_cases]) if doc_cases else None
            ),
            "retrieval_latency_p50": percentile([r.retrieval_latency for r in answered], 50),
            "retrieval_latency_p95": percentile([r.retrieval_latency for r in answered], 95),
            "generation_latency_p50": (
                percentile(
                    [r.generation_latency for r in answered if r.generation_latency is not None],
                    50,
                )
                if any(r.generation_latency is not None for r in answered)
                else None
            ),
            "generation_latency_p95": (
                percentile(
                    [r.generation_latency for r in answered if r.generation_latency is not None],
                    95,
                )
                if any(r.generation_latency is not None for r in answered)
                else None
            ),
        }


def build_retrieval_service(
    corpus_path: Path,
    *,
    sparse_top_k: int,
    document_retrieval: Any = None,
    embedder: Any = None,
) -> RetrievalService:
    """Assemble the retrieval pipeline; ``sparse_top_k=0`` yields dense-only.

    ``embedder`` defaults to the deterministic HashingEmbedder (offline,
    reproducible baseline). Pass the BGE embedder to measure the production
    semantic configuration.
    """
    store = ChunkStore.from_jsonl(corpus_path)
    sparse = Bm25SparseIndex(store.chunks)
    dense = CosineDenseIndex(store.chunks, embedder or HashingEmbedder())
    # The semantic-relevance confidence band (RELEVANCE_FLOOR/SATURATION) is
    # calibrated for BGE cosine scores. The offline HashingEmbedder baseline
    # has a different cosine scale (on-target hits score ~0.3), so reusing
    # the band would zero every confidence and refuse every case — the gate
    # is disabled here; a real embedder (BGE) keeps the production band.
    use_gate = embedder is not None
    return RetrievalService(
        store,
        dense,
        sparse,
        dense_top_k=20,
        sparse_top_k=sparse_top_k,
        confidence_threshold=0.1,
        document_retrieval=document_retrieval,
        relevance_floor=RELEVANCE_FLOOR if use_gate else None,
        relevance_saturation=RELEVANCE_SATURATION if use_gate else None,
    )


def _retrieved_sections(evidence: RetrievedEvidence) -> list[str]:
    sections: list[str] = []
    for scored in evidence.results:
        section = scored.chunk.section_number
        if section and section not in sections:
            sections.append(section)
    return sections


async def _setup_document_service() -> Any:
    """Create a session workspace and ingest the fixture document."""
    import tempfile

    from app.documents.ingestion import DocumentWorkspace, _InMemoryDocumentIndex
    from app.documents.service import BackgroundJobRunner, DocumentService
    from app.documents.storage import DocumentFileStorage
    from app.documents.store import DocumentStore

    storage_root = Path(tempfile.mkdtemp(prefix="nyaya-eval-"))
    store = DocumentStore()
    index = _InMemoryDocumentIndex()
    workspace = DocumentWorkspace(store, index, HashingEmbedder())
    service = DocumentService(
        store,
        DocumentFileStorage(storage_root),
        workspace,
        runner=BackgroundJobRunner(),
        allowed_types={"application/pdf"},
        max_size_bytes=20 * 1024 * 1024,
    )
    from tests.documents.pdf_fixtures import make_pdf

    document = await service.upload(
        session_id=SESSION_ID,
        filename="rental-notice.pdf",
        content_type="application/pdf",
        data=make_pdf(FIXTURE_DOCUMENT_PAGES),
    )
    for _ in range(100):
        status = service.status(session_id=SESSION_ID, document_id=document.document_id)
        if status.status.value == "ready":
            break
        await asyncio.sleep(0.05)
    else:
        raise RuntimeError("fixture document never reached ready state")
    from app.documents.retrieval import DocumentRetrievalService

    return DocumentRetrievalService(index, HashingEmbedder())


async def run_evaluation(
    golden_set_path: Path,
    corpus_path: Path,
    *,
    provider: Any = None,
    embedder: Any = None,
) -> dict[str, Any]:
    """Run the golden set under two configurations (dense-only vs hybrid).

    ``embedder`` (None = HashingEmbedder baseline) selects the dense backend;
    pass the BGE embedder to measure the production semantic configuration.
    """
    cases = load_golden_set(golden_set_path)
    document_retrieval = await _setup_document_service()

    # Configuration A: dense-only (sparse pool disabled) — D-048.
    # Configuration B: hybrid dense + sparse + RRF (the shipped default).
    # Document retrieval is session-scoped and identical across both.
    configurations = {
        "dense_only": build_retrieval_service(
            corpus_path,
            sparse_top_k=0,
            document_retrieval=document_retrieval,
            embedder=embedder,
        ),
        "hybrid": build_retrieval_service(
            corpus_path,
            sparse_top_k=20,
            document_retrieval=document_retrieval,
            embedder=embedder,
        ),
    }

    run_results: list[RunResult] = []
    for name, service in configurations.items():
        result = await _run_configuration(name, service, cases, provider)
        run_results.append(result)

    return {
        "golden_set": str(golden_set_path),
        "corpus": str(corpus_path),
        "deterministic": provider is None,
        "llm_mode": provider is not None,
        "configurations": [r.summary() for r in run_results],
    }


async def _run_configuration(
    name: str,
    service: RetrievalService,
    cases: list[GoldenCase],
    provider: Any,
) -> RunResult:
    result = RunResult(config_name=name)
    generation = GenerationService(provider) if provider is not None else None
    for case in cases:
        case_result = await _run_case(case, service, generation)
        result.case_results.append(case_result)
        if case_result.error:
            result.failures.append(f"{case.id}: {case_result.error}")
    return result


async def _run_case(
    case: GoldenCase,
    service: RetrievalService,
    generation: GenerationService | None,
) -> CaseResult:
    outcome = CaseResult(case_id=case.id)
    try:
        started = time.perf_counter()
        evidence = service.retrieve(case.question, session_id=SESSION_ID)
        outcome.retrieval_latency = time.perf_counter() - started

        sections = _retrieved_sections(evidence)
        if case.expected_sections:
            outcome.recall_at_5 = recall_at_k(sections, case.expected_sections, 5)
            outcome.recall_at_10 = recall_at_k(sections, case.expected_sections, 10)
            outcome.reciprocal_rank = reciprocal_rank(sections, case.expected_sections)
        outcome.document_hit = bool(evidence.document_hits)
        refused = not evidence.sufficient
        outcome.refusal_correct = refusal_correctness(refused, case.must_refuse)

        if generation is not None and not refused:
            started = time.perf_counter()
            generated = await generation.answer(case.question, evidence)
            outcome.generation_latency = time.perf_counter() - started
            cited = [c.section_number for c in generated.citations.valid_citations]
            outcome.citation_accuracy = citation_accuracy(cited, sections, case.expected_sections)
            outcome.refusal_correct = refusal_correctness(generated.refused, case.must_refuse)
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"
    return outcome
