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

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.api.deps import get_check_registry
from app.core.health import CheckRegistry, CheckStatus

router = APIRouter(tags=["health"])


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
