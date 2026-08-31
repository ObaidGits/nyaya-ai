#!/usr/bin/env python
"""Evaluation CLI (IMPLEMENTATION_PLAN §8.2).

Runs the golden set end-to-end against the current development corpus and
writes machine-readable results to ``eval/results/``.

Usage (from the repository root, with the backend virtualenv active):

    python eval/run_eval.py                 # deterministic retrieval-only
    python eval/run_eval.py --llm           # + Ollama answer generation
    python eval/run_eval.py --corpus PATH --out PATH

Exit code is non-zero when any case fails, so CI never hides failures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.evaluation.runner import run_evaluation  # noqa: E402


def _print_summary(results: dict[str, object]) -> None:
    print(f"golden set : {results['golden_set']}")
    print(f"corpus     : {results['corpus']}")
    print(f"deterministic: {results['deterministic']}  llm_mode: {results['llm_mode']}")
    print()
    header = (
        f"{'config':<12}{'recall@5':>10}{'recall@10':>11}{'MRR':>8}"
        f"{'refusal':>9}{'cite-acc':>10}{'ret-p50':>9}{'ret-p95':>9}{'failed':>8}"
    )
    print(header)
    print("-" * len(header))
    for config in results["configurations"]:
        citation = config["citation_accuracy"]
        citation_text = f"{citation:.3f}" if citation is not None else "n/a"
        print(
            f"{config['config']:<12}"
            f"{config['recall_at_5']:>10.3f}"
            f"{config['recall_at_10']:>11.3f}"
            f"{config['mrr']:>8.3f}"
            f"{config['refusal_correctness']:>9.3f}"
            f"{citation_text:>10}"
            f"{config['retrieval_latency_p50'] * 1000:>8.1f}ms"
            f"{config['retrieval_latency_p95'] * 1000:>8.1f}ms"
            f"{config['failed_cases']:>8}"
        )
        for failure in config["failures"]:
            print(f"  FAILED {failure}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Nyaya golden-set evaluation")
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=REPO_ROOT / "eval" / "golden_set.jsonl",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "data" / "processed" / "bnss-dev_chunks.jsonl",
        help="Chunked corpus artifact (development corpus: BNSS)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Result JSON path (default eval/results/<timestamp>.json)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable Ollama answer generation for citation/refusal answer metrics",
    )
    parser.add_argument(
        "--bge",
        action="store_true",
        help="Use the open-weight BGE embedder (production semantic config) "
        "instead of the deterministic hashing baseline",
    )
    args = parser.parse_args()

    if not args.golden_set.exists():
        print(f"golden set not found: {args.golden_set}", file=sys.stderr)
        return 2
    if not args.corpus.exists():
        print(f"corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    provider = None
    if args.llm:
        from app.core.config import get_settings
        from app.llm.ollama import OllamaProvider

        settings = get_settings()
        provider = OllamaProvider(settings.llm_base_url, settings.llm_model or "qwen2.5:3b")

    embedder = None
    if args.bge:
        from app.ingestion.embeddings import BgeEmbedder

        embedder = BgeEmbedder()
        embedder.embed_texts(["warmup"])  # load the model before timing starts

    results = asyncio.run(
        run_evaluation(args.golden_set, args.corpus, provider=provider, embedder=embedder)
    )
    results["executed_at"] = datetime.now(timezone.utc).isoformat()

    out_path = args.out or (
        REPO_ROOT / "eval" / "results" / f"evaluation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n")

    _print_summary(results)
    print(f"\nresults written to {out_path}")

    failed = sum(c["failed_cases"] for c in results["configurations"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
