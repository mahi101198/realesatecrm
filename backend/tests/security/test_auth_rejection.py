"""Security foundation tests for token verification and rejection."""

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.exceptions import UnauthorizedError
from app.core.security import extract_bearer_token, verify_supabase_token

SECRET = "test-jwt-secret-key-super-secret-minimum-32-chars"


def test_missing_auth_header_rejection() -> None:
    """Verify missing authorization header raises UnauthorizedError."""
    with pytest.raises(UnauthorizedError) as exc_info:
        extract_bearer_token(None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "MISSING_TOKEN"


def test_malformed_auth_header_rejection() -> None:
    """Verify malformed authorization header raises UnauthorizedError."""
    with pytest.raises(UnauthorizedError) as exc_info:
        extract_bearer_token("InvalidBearerScheme xyz")
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_HEADER_FORMAT"


def test_invalid_token_signature_rejection() -> None:
    """Verify invalid token signature raises UnauthorizedError."""
    bogus_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.bogus_signature"
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(bogus_token)
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_TOKEN"


def test_expired_token_rejection() -> None:
    token = jwt.encode(
        {
            "sub": "abc",
            "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "TOKEN_EXPIRED"
