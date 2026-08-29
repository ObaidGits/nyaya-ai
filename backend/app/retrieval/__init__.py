"""Retrieval package (REQUIREMENTS A2-*/A3-*, ARCHITECTURE §10-§16)."""

from app.retrieval.dense import CosineDenseIndex, DenseRetriever, QdrantDenseRetriever
from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import (
    MetadataFilter,
    RetrievalError,
    RetrievalRoute,
    RetrievedEvidence,
    ScoredChunk,
    SectionIntent,
)
from app.retrieval.rrf import rrf_fuse
from app.retrieval.service import RetrievalService
from app.retrieval.sparse import Bm25SparseIndex, SparseRetriever
from app.retrieval.store import ChunkStore

__all__ = [
    "Bm25SparseIndex",
    "ChunkStore",
    "CosineDenseIndex",
    "DenseRetriever",
    "MetadataFilter",
    "QdrantDenseRetriever",
    "RetrievalError",
    "RetrievalRoute",
    "RetrievalService",
    "RetrievedEvidence",
    "ScoredChunk",
    "SectionIntent",
    "SparseRetriever",
    "classify_route",
    "detect_section_intent",
    "rrf_fuse",
]
