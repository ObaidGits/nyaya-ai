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
from pathlib import Path

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


class StorageCheck(DependencyCheck):
    """Verify the upload storage directory exists and is writable (D-032)."""

    name = "storage"

    def __init__(self, settings: Settings) -> None:
        self._storage_dir = Path(settings.storage_dir)

    async def check(self) -> CheckResult:
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            probe = self._storage_dir / ".readiness-probe"
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"storage dir not writable: {type(exc).__name__}",
            )
        return CheckResult(name=self.name, status=CheckStatus.OK, detail="storage writable")


class RedisCheck(DependencyCheck):
    """Verify Redis answers PING (D-030: queue/rate-limit/document backend).

    Only registered when the deployment actually uses Redis
    (documents_backend="redis"); an in-memory deployment must not report a
    dependency it does not have.
    """

    name = "redis"

    def __init__(self, url: str, timeout: float = 3.0) -> None:
        self._url = url
        self._timeout = timeout

    async def check(self) -> CheckResult:
        import asyncio

        import redis as redis_module

        def _ping() -> None:
            client = redis_module.Redis.from_url(self._url, socket_timeout=self._timeout)
            try:
                client.ping()
            finally:
                client.close()

        try:
            await asyncio.wait_for(asyncio.to_thread(_ping), timeout=self._timeout)
        except Exception:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url} unreachable",
            )
        return CheckResult(name=self.name, status=CheckStatus.OK, detail=f"{self._url} reachable")


class _HttpDependencyCheck(DependencyCheck):
    """Shared async HTTP probe for URL-configured dependencies."""

    def __init__(self, url: str, timeout: float = 3.0) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout

    async def _probe(self, path: str, *, max_status: int = 399) -> CheckResult:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._url}{path}")
        except httpx.HTTPError:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url} unreachable",
            )
        if response.status_code >= max_status:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url} returned {response.status_code}",
            )
        return CheckResult(name=self.name, status=CheckStatus.OK, detail=f"{self._url} reachable")


class VectorDBCheck(_HttpDependencyCheck):
    """Verify the vector DB answers its health endpoint (D-030)."""

    name = "vector_db"

    async def check(self) -> CheckResult:
        return await self._probe("/healthz")


class ModelProviderCheck(_HttpDependencyCheck):
    """Verify the LLM provider endpoint answers and the model is present.

    For Ollama the check goes beyond transport: ``/api/tags`` is parsed and
    the configured model name must actually be pulled, so a reachable
    server without the model is reported as failing rather than healthy
    (the "brain" the UI reports as active must be genuinely loadable).
    Other providers are probed at their root, where any sub-server-error
    response still proves the endpoint is up.
    """

    name = "model"

    def __init__(
        self,
        url: str,
        provider: str,
        model: str | None = None,
        timeout: float = 3.0,
    ) -> None:
        super().__init__(url, timeout)
        self._provider = provider
        self._model = model

    async def check(self) -> CheckResult:
        if self._provider != "ollama":
            return await self._probe("/", max_status=500)
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._url}/api/tags")
        except httpx.HTTPError:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url} unreachable",
            )
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url} returned {response.status_code}",
            )
        if self._model is None:
            # No model configured to verify; transport is all we can assert.
            return CheckResult(name=self.name, status=CheckStatus.OK, detail="provider reachable")
        try:
            tags = response.json().get("models") or []
        except ValueError:
            return CheckResult(
                name=self.name,
                status=CheckStatus.FAIL,
                detail=f"{self._url}/api/tags returned invalid JSON",
            )
        names = {str(tag.get("name", "")) for tag in tags}
        names |= {str(tag.get("model", "")) for tag in tags}
        if self._model in names or any(name.startswith(f"{self._model}:") for name in names):
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                detail=f"model {self._model} available",
            )
        return CheckResult(
            name=self.name,
            status=CheckStatus.FAIL,
            detail=f"model {self._model} not present on provider",
        )
