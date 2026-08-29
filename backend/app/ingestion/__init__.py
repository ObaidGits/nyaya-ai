"""Structure-aware ingestion package (REQUIREMENTS R-004, SRC-*, A1-*).

Public API for the ingestion pipeline and the scripts/ingest.py entry point.
"""

from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.embeddings import BgeEmbedder, EmbeddingProvider, NullEmbedder
from app.ingestion.extract import PageExtractor, PypdfPageExtractor
from app.ingestion.index_store import ChunkIndex, JsonlChunkSink, QdrantChunkIndex
from app.ingestion.models import (
    Chunk,
    CorpusSpec,
    IngestionResult,
    PageText,
    ParsedAct,
    Section,
    SourceIdentity,
    ValidationResult,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.validation import SourceValidationError

__all__ = [
    "BgeEmbedder",
    "Chunk",
    "ChunkIndex",
    "CorpusSpec",
    "EmbeddingProvider",
    "IngestionPipeline",
    "IngestionResult",
    "JsonlChunkSink",
    "NullEmbedder",
    "PageExtractor",
    "PageText",
    "ParsedAct",
    "PypdfPageExtractor",
    "QdrantChunkIndex",
    "Section",
    "SourceIdentity",
    "SourceValidationError",
    "StructureAwareChunker",
    "ValidationResult",
]
