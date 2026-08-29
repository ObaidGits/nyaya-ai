"""Dependency health-check contracts for the readiness endpoint.

The readiness endpoint (``GET /api/v1/health/ready``) reports whether required
runtime dependencies are available (ARCHITECTURE.md §40). Concrete checks for
the vector DB, model provider and storage arrive with the phases that
implement those dependencies (REQUIREMENTS.md D-030/D-031/D-032).

Phase 1 registers a single ``configuration`` check verifying that the
application's own configuration remains valid. No dependency is ever reported
healthy without a real check having run: a check that raises is reported as a
failure, never swallowed.
"""

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ValidationError

from app.core.config import Settings


class CheckStatus(StrEnum):
    """Outcome of a single dependency check."""

    OK = "ok"
    FAIL = "fail"


class CheckResult(BaseModel):
    """Result of one dependency check."""

    name: str
    status: CheckStatus
    detail: str | None = None


class DependencyCheck(ABC):
    """A named readiness check for one runtime dependency."""

    name: str = "dependency"

    @abstractmethod
    async def check(self) -> CheckResult:
        """Run the check and report the honest outcome."""


class CheckRegistry:
    """Ordered collection of dependency checks executed by readiness."""

    def __init__(self, checks: list[DependencyCheck] | None = None) -> None:
        self._checks: list[DependencyCheck] = list(checks or [])

    def add(self, check: DependencyCheck) -> None:
        self._checks.append(check)

    def names(self) -> list[str]:
        return [check.name for check in self._checks]

    async def run_all(self) -> list[CheckResult]:
        """Run every registered check.

        A check that raises is converted into a failing result rather than
        aborting the readiness evaluation or being silently skipped.
        """
        results: list[CheckResult] = []
        for check in self._checks:
            try:
                results.append(await check.check())
            except Exception as exc:
                results.append(
                    CheckResult(
                        name=check.name,
                        status=CheckStatus.FAIL,
                        detail=f"check raised {type(exc).__name__}",
                    )
                )
        return results


class ConfigurationCheck(DependencyCheck):
    """Verify the application's runtime configuration is structurally valid."""

    name = "configuration"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self) -> CheckResult:
        try:
            Settings.model_validate(self._settings.model_dump())
        except ValidationError:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail="application configuration is invalid",
            )
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            detail="application configuration loaded",
        )
