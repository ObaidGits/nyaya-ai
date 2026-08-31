"""Admin corpus replacement (Settings page; DECISIONS.md D-080).

Reuses the existing ingestion pipeline end-to-end: PDF upload → extraction →
content-based source validation (``CorpusSpec.bns``: a BNSS PDF is rejected
as BNS, filenames and user-supplied labels are never trusted) → parsing →
structure validation → chunking → artifact write → new retrieval service →
verification query → atomic activation. The active corpus is swapped only
after every step succeeds; any failure leaves the previous corpus serving.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.ingestion.index_store import JsonlChunkSink
from app.ingestion.models import CorpusSpec
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.validation import SourceValidationError

logger = logging.getLogger(__name__)


class CorpusReplacementError(Exception):
    """Safe, client-facing failure during corpus replacement."""


def _ingest_to_artifact(pdf_path: Path, artifact_path: Path) -> dict[str, Any]:
    """Run the full validated pipeline into a fresh chunk artifact."""
    from app.ingestion.extract import PypdfPageExtractor

    pipeline = IngestionPipeline(
        spec=CorpusSpec.bns(),
        extractor=PypdfPageExtractor(),
        index=JsonlChunkSink(artifact_path),
    )
    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    result = pipeline.run(pdf_path, ingested_at)
    return {
        "act": pipeline.spec.act,
        "act_short": pipeline.spec.act_short,
        "filename": result.source.filename,
        "sha256": result.source.sha256,
        "pages": result.source.page_count,
        "sections": result.section_count,
        "chunks": result.chunk_count,
        "ingested_at": ingested_at,
        "validation": {
            "ok": result.validation.ok,
            "warnings": result.validation.warnings,
        },
        "artifact_path": str(artifact_path),
    }


def build_replacement(
    pdf_bytes: bytes, settings: Settings, *, artifacts_dir: Path
) -> tuple[dict[str, Any], Path]:
    """Validate + ingest an uploaded PDF; return (manifest, artifact path).

    Raises CorpusReplacementError with a safe message on any failure; the
    active corpus is never touched here.
    """
    with tempfile.TemporaryDirectory(prefix="nyaya-corpus-") as tmp:
        staged = Path(tmp) / "upload.pdf"
        staged.write_bytes(pdf_bytes)
        try:
            artifact_dir = artifacts_dir
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = (
                artifact_dir / f"corpus-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.jsonl"
            )
            manifest = _ingest_to_artifact(staged, artifact_path)
        except SourceValidationError as exc:
            raise CorpusReplacementError(str(exc)) from exc
        except Exception as exc:  # extraction/parse/OSError — never leak paths
            logger.warning("corpus replacement ingestion failed", exc_info=True)
            raise CorpusReplacementError(
                "The uploaded PDF could not be ingested as the required corpus."
            ) from exc
    return manifest, Path(manifest["artifact_path"])


def verify_artifact(retrieval_service: object) -> None:
    """Smoke-verify the freshly built service answers a core statute query.

    A service that cannot retrieve anything must never become active.
    """
    try:
        results = retrieval_service.retrieve("punishment for murder")  # type: ignore[attr-defined]
    except Exception as exc:
        raise CorpusReplacementError(
            "The new corpus failed verification: retrieval did not respond."
        ) from exc
    if not results.results:
        raise CorpusReplacementError(
            "The new corpus failed verification: no results for a core statute query."
        )


__all__ = [
    "CorpusReplacementError",
    "build_replacement",
    "verify_artifact",
]
