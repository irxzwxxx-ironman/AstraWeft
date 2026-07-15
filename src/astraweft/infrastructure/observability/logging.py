"""JSON file logging with recursive secret redaction."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from astraweft.application.tracing import current_trace_id

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token|credential|signed[_-]?url)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_REDACTED = "[REDACTED]"
_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-safe structure with known sensitive values removed."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer [REDACTED]", value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class JsonLogFormatter(logging.Formatter):
    """Format one redacted JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        trace_id = current_trace_id()
        if trace_id is not None:
            payload["trace_id"] = trace_id
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["context"] = redact(extras)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(log_dir: Path, level: str = "INFO") -> Path:
    """Configure the AstraWeft logger and return the active log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "astraweft.jsonl"
    logger = logging.getLogger("astraweft")
    logger.disabled = False
    logger.setLevel(level)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = JsonLogFormatter()
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return log_path
