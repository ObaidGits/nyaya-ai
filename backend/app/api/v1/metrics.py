"""Prometheus metrics endpoint (ARCHITECTURE §41, F-025..F-033)."""

from fastapi import APIRouter, Response

from app.observability.metrics import REGISTRY

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics() -> Response:
    """Expose application metrics in the Prometheus text exposition format."""
    return Response(content=REGISTRY.render(), media_type="text/plain; version=0.0.4")
