"""Structured logging and redaction tests."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from astraweft.application.tracing import TraceContext
from astraweft.infrastructure.observability.logging import (
    JsonLogFormatter,
    configure_logging,
    redact,
)


class StaticIds:
    def new(self) -> str:
        return "trace-log-test"


def test_redact_recurses_and_preserves_safe_context() -> None:
    payload = {
        "api_key": "top-secret",
        "nested": {"password": "secret", "safe": 42},
        "items": ["Bearer abc.DEF-123", object()],
    }

    result = redact(payload)

    assert result["api_key"] == "[REDACTED]"
    assert result["nested"] == {"password": "[REDACTED]", "safe": 42}
    assert result["items"][0] == "Bearer [REDACTED]"
    assert "object at" in result["items"][1]


def test_json_formatter_redacts_message_extras_and_exception() -> None:
    formatter = JsonLogFormatter()
    try:
        raise ValueError("Bearer abc123")
    except ValueError:
        record = logging.getLogger("astraweft.test").makeRecord(
            "astraweft.test",
            logging.ERROR,
            __file__,
            1,
            "request failed: Bearer abc123",
            (),
            exc_info=sys.exc_info(),
            extra={"authorization": "unsafe", "provider": "demo"},
        )

    payload = json.loads(formatter.format(record))
    serialized = json.dumps(payload)
    assert "abc123" not in serialized
    assert "unsafe" not in serialized
    assert payload["context"]["provider"] == "demo"
    assert payload["level"] == "ERROR"


def test_configure_logging_replaces_handlers_and_writes_json(tmp_path: Path) -> None:
    logger = logging.getLogger("astraweft")
    logger.addHandler(logging.NullHandler())

    log_path = configure_logging(tmp_path, "DEBUG")
    with TraceContext(StaticIds()).start():
        logger.info("provider_ready", extra={"token": "never-write", "provider": "local"})
    for handler in logger.handlers:
        handler.flush()

    line = log_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["message"] == "provider_ready"
    assert payload["context"]["token"].startswith("[")
    assert "never-write" not in line
    assert payload["context"]["provider"] == "local"
    assert payload["trace_id"] == "trace-log-test"
    assert len(logger.handlers) == 1
