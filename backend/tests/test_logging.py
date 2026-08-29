"""Structured logging tests (REQUIREMENTS.md D-051, SEC-005)."""

import json
import logging

import pytest
from app.core.logging import JsonFormatter, redact, setup_logging
from app.core.request_id import _request_id


def make_record(**kwargs: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="nyaya.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=str(kwargs.pop("msg", "hello")),
        args=(),
        exc_info=kwargs.pop("exc_info", None),  # type: ignore[arg-type]
    )


def test_formatter_outputs_parseable_json() -> None:
    payload = json.loads(JsonFormatter().format(make_record()))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "nyaya.test"
    assert payload["message"] == "hello"
    assert payload["timestamp"]


def test_formatter_includes_record_request_id() -> None:
    record = make_record()
    record.request_id = "record-request-id"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "record-request-id"


def test_formatter_falls_back_to_context_request_id() -> None:
    token = _request_id.set("context-request-id")
    try:
        payload = json.loads(JsonFormatter().format(make_record()))
    finally:
        _request_id.reset(token)
    assert payload["request_id"] == "context-request-id"


def test_formatter_includes_structured_fields_and_exception() -> None:
    record = make_record(
        msg="request completed",
        exc_info=(ValueError, ValueError("boom"), None),
    )
    record.method = "GET"  # type: ignore[attr-defined]
    record.path = "/api/v1/health"  # type: ignore[attr-defined]
    record.status_code = 200  # type: ignore[attr-defined]
    record.duration_ms = 1.5  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/v1/health"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.5
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


def test_redact_masks_sensitive_keys() -> None:
    data = {
        "llm_api_key": "super-secret-value",
        "password": "hunter2",
        "token": "abc123",
        "nested": {"user_token": "xyz", "safe": "visible"},
        "items": [{"api_key": "k"}],
        "plain": "untouched",
    }
    result = redact(data)
    assert result["llm_api_key"] == "***redacted***"
    assert result["password"] == "***redacted***"
    assert result["token"] == "***redacted***"
    assert result["nested"]["user_token"] == "***redacted***"
    assert result["nested"]["safe"] == "visible"
    assert result["items"][0]["api_key"] == "***redacted***"
    assert result["plain"] == "untouched"
    assert "super-secret-value" not in json.dumps(result)


def test_redact_masks_secret_str_serialisation() -> None:
    assert redact("**********masked") == "***redacted***"


def test_setup_logging_is_idempotent(settings) -> None:
    setup_logging(settings)
    handler_count = len(logging.getLogger().handlers)
    setup_logging(settings)
    assert len(logging.getLogger().handlers) == handler_count


def test_setup_logging_preserves_other_handlers(settings, caplog: pytest.LogCaptureFixture) -> None:
    setup_logging(settings)
    caplog.set_level(logging.INFO, logger="nyaya.setup-check")
    logging.getLogger("nyaya.setup-check").info("still captured")
    assert any(r.getMessage() == "still captured" for r in caplog.records)
