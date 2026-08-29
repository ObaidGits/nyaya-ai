"""Request ID handling.

Every HTTP request receives a request identifier that:

* is accepted from the incoming ``X-Request-ID`` header when it uses a safe
  format,
* is generated (UUID4) when absent or malformed,
* is attached to the response,
* is stored in a :class:`contextvars.ContextVar` so structured log records and
  later-phase retrieval/generation code can correlate a request end to end
  (ARCHITECTURE.md §39, REQUIREMENTS.md D-052).

Incoming identifiers are strictly validated before use so untrusted header
values are never echoed into responses or interpolated into logs.
"""

import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

# Safe identifier format: 1-64 characters of letters, digits, underscore, dash.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_access_logger = logging.getLogger("nyaya.access")


def generate_request_id() -> str:
    """Return a new random request identifier."""
    return str(uuid.uuid4())


def sanitize_request_id(value: str | None) -> str | None:
    """Return ``value`` if it is a safe request identifier, else ``None``."""
    if value is None:
        return None
    return value if _REQUEST_ID_PATTERN.fullmatch(value) else None


def get_request_id() -> str | None:
    """Return the request identifier for the current request context, if any."""
    return _request_id.get()


class RequestIDMiddleware:
    """Pure ASGI middleware attaching a request ID to every HTTP request.

    The identifier is exposed to the response (header), to log records
    (context variable), and to downstream request handlers
    (``request.state.request_id``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = sanitize_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        if request_id is None:
            request_id = generate_request_id()

        # Make the identifier visible downstream (handlers) and outward
        # (log records) within this request's context.
        token = _request_id.set(request_id)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = list(message.get("headers", []))
                already_set = any(key.lower() == b"x-request-id" for key, _ in raw_headers)
                if not already_set:
                    raw_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = raw_headers
            await send(message)

        def _log(level: int, event: str, *, extra_status: bool = False) -> None:
            extra: dict[str, Any] = {
                "request_id": request_id,
                "method": scope.get("method"),
                "path": scope.get("path"),
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            if extra_status:
                extra["status_code"] = status_code
            _access_logger.log(level, event, extra=extra)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # The request ID is intentionally NOT reset here: the outermost
            # error handler still needs it to build the error response. The
            # traceback goes to the server-side structured log only; the
            # client still only ever sees the generic error envelope.
            _access_logger.log(
                logging.ERROR,
                "request failed",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            raise

        _request_id.reset(token)
        _log(logging.INFO, "request completed", extra_status=True)
