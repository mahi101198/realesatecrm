"""Structured Logging Module with Automatic Redaction."""

import logging
import sys
from typing import Any

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - fallback for older package layouts
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[attr-defined]

from app.core.config import settings
from app.core.constants import SENSITIVE_KEYS
from app.core.request_context import get_request_id


def redact_data(data: Any) -> Any:
    """Recursively redact sensitive key values in dictionaries/lists."""
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_data(value)
        return redacted
    if isinstance(data, list):
        return [redact_data(item) for item in data]
    return data


class RedactingJsonFormatter(JsonFormatter):
    """Custom JSON formatter that redacts sensitive values and attaches correlation request ID."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # Standard structured fields
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["environment"] = settings.APP_ENV

        # Inject Request ID from ContextVar if available
        request_id = get_request_id()
        if request_id:
            log_record["request_id"] = request_id

        # Redact sensitive data from log record
        for key in list(log_record.keys()):
            if str(key).lower() in SENSITIVE_KEYS:
                log_record[key] = "[REDACTED]"
            else:
                log_record[key] = redact_data(log_record[key])


def setup_logging() -> None:
    """Configure application-wide structured JSON logging."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = RedactingJsonFormatter(
        "%(timestamp)s %(level)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Silence noisy loggers
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = True
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DEBUG else logging.WARNING
    )
