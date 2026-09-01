"""Consistent API error handling (REQUIREMENTS.md Part D).

Every error response uses a single envelope::

    {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed.",
            "request_id": "…",
            "details": [...]
        }
    }

Handled categories:

* request validation errors (422),
* application errors (:class:`AppError` subclasses),
* HTTP errors raised by the framework (404, 405, ...),
* unexpected server errors (500).

Unexpected errors are logged server-side with full exception information, but
clients receive a generic message — stack traces and internal details never
leave the server. Domain-specific legal errors are intentionally not defined
here; they arrive with later phases.
"""

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger("nyaya.errors")


class ErrorDetail(BaseModel):
    """A single, client-safe detail entry."""

    location: str | None = None
    message: str | None = None


class ErrorBody(BaseModel):
    """The error envelope body."""

    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Top-level error response model."""

    error: ErrorBody


class AppError(Exception):
    """Base class for application errors raised by Nyaya code."""

    status_code: int = 500
    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class ServiceUnavailableError(AppError):
    """A required runtime dependency is unavailable or not configured."""

    status_code = 503
    code = "SERVICE_UNAVAILABLE"


class LLMProviderNotConfiguredError(ServiceUnavailableError):
    """The configured LLM provider cannot be resolved."""

    code = "LLM_PROVIDER_NOT_CONFIGURED"


class LLMRateLimitError(AppError):
    """The generation provider is rate limiting requests (HTTP 429).

    Carries 503 rather than 429 because the upstream limit is the server's,
    not this client's: the client should retry shortly, not back off as if
    it had misbehaved. Streams are HTTP 200 anyway — the code is the signal.
    """

    status_code = 503
    code = "LLM_RATE_LIMITED"

    def __init__(
        self,
        message: str = "The generation provider is rate limiting requests. "
        "Please try again shortly.",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)


class LLMTimeoutError(AppError):
    """The generation provider timed out before responding."""

    status_code = 504
    code = "LLM_TIMEOUT"

    def __init__(
        self,
        message: str = "The generation provider timed out before responding. Please try again.",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


def _build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    request_id = get_request_id()
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details or [])
    )
    response = JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))
    # For responses produced outside the request-ID middleware (the outermost
    # 500 handler), attach the header here; the middleware skips duplicates.
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    # NOTE: the extra key must not be named "message" — that clashes with the
    # reserved LogRecord.message attribute, which makes logging raise
    # "Cannot override" KeyError and masks the original error.
    logger.warning(
        "application error raised",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "error_message": exc.message,
        },
    )
    return _build_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _build_error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
        details=safe_error_details(exc.errors()),
    )


def safe_error_details(errors: Sequence[dict[str, Any]]) -> list[ErrorDetail]:
    """Convert raw validation error dictionaries to client-safe details.

    Input values are deliberately excluded: request payloads may contain
    sensitive data that must not be echoed back or logged.
    """
    return [
        ErrorDetail(
            location=".".join(str(part) for part in error.get("loc", ())),
            message=str(error.get("msg")),
        )
        for error in errors
    ]


async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    return _build_error_response(status_code=exc.status_code, code=code, message=str(exc.detail))


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # The full traceback is logged by the request-ID middleware as the
    # exception propagates; here we log the handler-level classification.
    logger.error(
        "unhandled exception converted to error response",
        extra={"exception_type": type(exc).__name__},
    )
    return _build_error_response(
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
    )


async def handle_dependency_error(request: Request, exc: Exception) -> JSONResponse:
    """A backend dependency (e.g. Redis) is down: report 503, not 500.

    Clients can distinguish "the service is broken" from "a backing store
    is temporarily unreachable" and retry accordingly.
    """
    logger.warning(
        "dependency error converted to 503",
        extra={"exception_type": type(exc).__name__, "path": request.url.path},
    )
    return _build_error_response(
        status_code=503,
        code="DEPENDENCY_UNAVAILABLE",
        message="A backing service is temporarily unavailable. Please retry.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the consistent error handlers on the application."""
    app.add_exception_handler(AppError, handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    try:
        # Redis backs the document store/index in production (D-030). When it
        # is down the API reports 503 DEPENDENCY_UNAVAILABLE instead of 500.
        import redis

        app.add_exception_handler(
            redis.RedisError,  # type: ignore[arg-type]
            handle_dependency_error,
        )
    except ImportError:
        pass
    app.add_exception_handler(Exception, handle_unexpected_error)
