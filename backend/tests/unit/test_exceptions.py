"""Unit tests for exception hierarchy and error code mapping."""

import pytest

from app.core.exceptions import (
    AppError,
    BusinessRuleError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


def test_app_error_defaults():
    """Verify base AppError status code and default parameters."""
    err = AppError()
    assert err.status_code == 500
    assert err.code == "INTERNAL_ERROR"
    assert err.message == "An unexpected error occurred."


@pytest.mark.parametrize(
    ("exc_class", "expected_status", "expected_code"),
    [
        (NotFoundError, 404, "NOT_FOUND"),
        (UnauthorizedError, 401, "UNAUTHORIZED"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (ConflictError, 409, "CONFLICT"),
        (ValidationError, 422, "VALIDATION_ERROR"),
        (BusinessRuleError, 422, "BUSINESS_RULE_VIOLATION"),
        (ExternalServiceError, 502, "EXTERNAL_SERVICE_ERROR"),
    ],
)
def test_specific_exception_types(exc_class, expected_status, expected_code):
    """Verify specific HTTP status code and error code mapping for custom exceptions."""
    err = exc_class(message="Custom message")
    assert err.status_code == expected_status
    assert err.code == expected_code
    assert err.message == "Custom message"
