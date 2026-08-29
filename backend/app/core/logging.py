"""Structured JSON logging for the Nyaya backend (REQUIREMENTS.md D-051).

All application logging flows through the standard :mod:`logging` module with a
JSON formatter, producing machine-readable records with:

* UTC timestamp,
* severity level,
* logger name (component),
* message,
* request ID (from the request context, when available),
* request-scoped fields (method, path, status code, duration),
* full exception information (server-side only).

The formatter redacts values of sensitive keys (API keys, passwords, tokens,
credentials) so secrets never reach log output.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.request_id import get_request_id

# Structured fields attached to log records by application code. "error_message"
# is used instead of "message" because "message" is a reserved LogRecord
# attribute and cannot be passed via logging extra=.
_STRUCTURED_FIELDS = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_code",
    "error_message",
)

# Substrings identifying sensitive keys whose values must never be logged.
_SENSITIVE_KEY_MARKERS = ("api_key", "apikey", "password", "secret", "token", "credential")


def redact(value: Any) -> Any:
    """Recursively mask values of sensitive keys and SecretStr-like objects."""
    if isinstance(value, dict):
        return {
            key: ("***redacted***" if _is_sensitive_key(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and value.startswith("**********"):
        # Pydantic SecretStr serialisation; keep masked.
        return "***redacted***"
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id

        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# Handlers previously installed by this module, tracked so repeated calls to
# setup_logging do not duplicate handlers or remove handlers owned by others
# (e.g. pytest's caplog).
_installed_handlers: list[logging.Handler] = []


def setup_logging(settings: Settings) -> None:
    """Configure root logging to emit structured JSON on stdout."""
    root = logging.getLogger()

    for handler in _installed_handlers:
        root.removeHandler(handler)
    _installed_handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    _installed_handlers.append(handler)
    root.setLevel(settings.log_level)
