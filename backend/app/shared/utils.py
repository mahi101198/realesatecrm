"""Shared Utility Functions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.constants import SENSITIVE_KEYS


def generate_request_id() -> str:
    """Generate a random UUID4 string for request tracking."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied dictionary with sensitive keys redacted."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if str(key).lower() in SENSITIVE_KEYS:
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        else:
            result[key] = value
    return result
