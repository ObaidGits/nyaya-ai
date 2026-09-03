"""Health and readiness endpoints (REQUIREMENTS.md D-028, D-029).

Liveness (``GET /api/v1/health``) answers only "is the API process alive?" and
deliberately depends on no external service (ARCHITECTURE.md §40).

Readiness (``GET /api/v1/health/ready``) runs the registered dependency checks
and reports an honest failure state (503) when any check fails. Phase 1
registers only the application configuration check; vector DB, model and
storage checks are added by the phases that implement those dependencies
(D-030/D-031/D-032).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.api.deps import get_check_registry
from app.core.health import CheckRegistry, CheckStatus

router = APIRouter(tags=["health"])

#: How long a provider probe result is reused (the UI polls every 30 s; the
#: probe must not hit the provider on every request from every client).
LLM_HEALTH_CACHE_SECONDS = 15.0


class HealthResponse(BaseModel):
    """Liveness response body."""

    status: Literal["ok"]


class ReadinessCheck(BaseModel):
    """One dependency check outcome."""

    status: CheckStatus
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness response body."""

    status: Literal["ok", "unavailable"]
    checks: dict[str, ReadinessCheck]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def liveness() -> HealthResponse:
    """Report whether the API process itself is alive."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(
    response: Response,
    registry: Annotated[CheckRegistry, Depends(get_check_registry)],
) -> ReadinessResponse:
    """Run registered dependency checks and report readiness honestly."""
    results = await registry.run_all()
    all_ok = all(result.status is CheckStatus.OK for result in results)
    response.status_code = 200 if all_ok else 503
    return ReadinessResponse(
        status="ok" if all_ok else "unavailable",
        checks={
            result.name: ReadinessCheck(status=result.status, detail=result.detail)
            for result in results
        },
    )


class LlmHealthResponse(BaseModel):
    """Public, authoritative LLM usability state (the "Brain" indicator).

    ``state`` is one of: not_configured, invalid_configuration, unavailable,
    degraded, healthy, error. "healthy" means the active provider is
    configured, authenticated AND its model is offered. ``config_source``
    says where the effective provider config comes from — the persisted
    admin console settings or the environment — so a stale env value can
    never be misread as the runtime config. Contains no secrets.
    """

    # Health endpoint state and details. Provider/model are operational
    # details (dev tool): SHOW_PROVIDER_DETAILS (default: on in local, off
    # in production via Settings.environment) controls whether they are
    # disclosed to anonymous callers.
    state: Literal[
        "not_configured",
        "invalid_configuration",
        "unavailable",
        "degraded",
        "healthy",
        "error",
    ]
    provider: str | None = None
    model: str | None = None
    detail: str = ""
    #: True/False after an explicit chat round-trip (D-096); None = not
    #: tested by this polled probe (chat is verified at configuration time).
    chat_verified: bool | None = None
    config_source: Literal["admin_console", "environment"] = "environment"


def _llm_config_source(request: Request) -> str:
    """ "admin_console" when persisted console settings select the provider."""
    store = getattr(request.app.state, "admin_store", None)
    if store is None:
        return "environment"
    return "admin_console" if "llm_provider" in store.load()["settings"] else "environment"


@router.get("/health/llm", response_model=LlmHealthResponse, summary="Active LLM provider health")
async def llm_health(request: Request) -> LlmHealthResponse:
    """Probe the ACTIVE provider (resolved at request time, so console
    changes apply immediately) and return its classified state.

    The result is cached briefly (``LLM_HEALTH_CACHE_SECONDS``) — the
    frontend polls every 30 s, and the probe must not hit the provider for
    every page load of every client. Settings changes invalidate it.
    """
    import time

    cached: tuple[float, LlmHealthResponse] | None = getattr(
        request.app.state, "llm_health_cache", None
    )
    now = time.monotonic()
    if cached is not None and now - cached[0] < LLM_HEALTH_CACHE_SECONDS:
        return cached[1]

    from app.api.deps import get_llm_provider
    from app.llm.base import ProviderHealth

    config_source = _llm_config_source(request)
    # SHOW_PROVIDER_DETAILS gates provider/model disclosure on this dev
    # endpoint; default mirrors the environment (local: visible, production:
    # hidden) so production does not name its LLM vendor/model to anonymous
    # callers. Set SHOW_PROVIDER_DETAILS=true|false to override either way.
    import os

    show_details = os.environ.get("SHOW_PROVIDER_DETAILS", "").strip().lower()
    if not show_details:
        show_details = "false" if request.app.state.settings.is_production else "true"
    show_details = show_details in ("1", "true", "yes")
    try:
        provider = get_llm_provider(request)
    except Exception:
        result = LlmHealthResponse(
            state="not_configured",
            detail="No usable LLM provider is configured.",
            config_source=config_source,
        )
    else:
        try:
            health: ProviderHealth = await provider.probe()
        except Exception:
            meta = provider.metadata()
            result = LlmHealthResponse(
                state="error",
                provider=meta.provider if show_details else None,
                model=meta.model if show_details else None,
                detail="The provider health probe failed unexpectedly.",
                config_source=config_source,
            )
        else:
            result = LlmHealthResponse(
                state=health.state.value,
                provider=health.provider if show_details else None,
                model=health.model if show_details else None,
                detail=health.detail,
                chat_verified=health.chat_verified,
                config_source=config_source,
            )
    request.app.state.llm_health_cache = (now, result)
    return result
