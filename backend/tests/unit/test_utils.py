"""Unit tests for utility functions."""

from app.shared.utils import generate_request_id, redact_dict, utcnow


def test_generate_request_id():
    """Verify generated request ID is non-empty string."""
    req_id = generate_request_id()
    assert isinstance(req_id, str)
    assert len(req_id) > 10


def test_utcnow():
    """Verify utcnow returns timezone-aware UTC datetime."""
    now = utcnow()
    assert now.tzinfo is not None


def test_redact_dict():
    """Verify dictionary sensitive keys are properly redacted."""
    raw = {
        "name": "John",
        "authorization": "Bearer token123",
        "password": "secretpassword",
        "nested": {
            "api_key": "key123",
            "city": "Mumbai",
        },
    }

    redacted = redact_dict(raw)
    assert redacted["name"] == "John"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["city"] == "Mumbai"
