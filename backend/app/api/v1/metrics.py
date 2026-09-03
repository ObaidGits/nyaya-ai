"""Prometheus metrics endpoint (ARCHITECTURE §41, F-025..F-033).

Access is authenticated, not public: metrics names, route volumes and
latency distributions are operational internals. Two accepted authorities:

* an admin session cookie (same auth as ``/api/v1/admin/*``), or
* a static bearer token in ``METRICS_TOKEN`` (scrape config).

When neither is configured (local dev: no admin credentials and no
``METRICS_TOKEN``), the endpoint stays open and a warning is logged ONCE —
fail-open only in an explicitly unconfigured dev instance, never silently
in production.
"""

from __future__ import annotations

import logging
import secrets as py_secrets

from fastapi import APIRouter, Header, Request, Response

from app.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])

_METRICS_TOKEN_HEADER = "Authorization"
_warned_unauthenticated = False


def _metrics_token() -> str | None:
    import os

    token = os.environ.get("METRICS_TOKEN", "").strip()
    return token or None


def _authorized(request: Request, token_header: str | None) -> bool:
    """Admin-cookie OR static-token authority; None when unconfigured."""
    from app.admin import auth

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and auth.admin_enabled(settings):
        # Reuse the admin guard semantics without its mutating-header
        # requirement: metrics reads are non-mutating.
        try:
            auth.require_admin(request, mutating=False)
            return True
        except Exception:
            pass  # fall through to the token check
    token = _metrics_token()
    if token is not None and token_header is not None:
        supplied = token_header.removeprefix("Bearer ").strip()
        if py_secrets.compare_digest(supplied, token):
            return True
    return False


@router.get("/metrics")
def get_metrics(request: Request, authorization: str | None = Header(default=None)) -> Response:
    """Expose application metrics in the Prometheus text exposition format."""
    global _warned_unauthenticated

    token_configured = _metrics_token() is not None
    admin_configured = False
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        from app.admin import auth

        admin_configured = auth.admin_enabled(settings)

    if admin_configured or token_configured:
        if not _authorized(request, authorization):
            from app.core.errors import AppError

            raise AppError(
                "Metrics access requires an admin session or the metrics token.",
                status_code=401,
                code="METRICS_UNAUTHORIZED",
            )
    elif not _warned_unauthenticated:
        _warned_unauthenticated = True
        logger.warning(
            "/api/v1/metrics is unauthenticated: set ADMIN_USERNAME/ADMIN_PASSWORD "
            "or METRICS_TOKEN to protect it"
        )
    return Response(content=REGISTRY.render(), media_type="text/plain; version=0.0.4")
