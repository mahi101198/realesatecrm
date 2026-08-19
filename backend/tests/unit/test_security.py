"""Unit tests for security helpers and JWT verification."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jose import jwt

from app.core.exceptions import UnauthorizedError
from app.core.security import extract_bearer_token, verify_supabase_token

SECRET = "test-jwt-secret-key-super-secret-minimum-32-chars"


def _encode(payload: dict[str, Any], secret: str = SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def test_extract_bearer_token_valid() -> None:
    """Verify valid Bearer header parsing."""
    assert extract_bearer_token("Bearer token-12345") == "token-12345"


def test_extract_bearer_token_missing() -> None:
    """Verify missing header raises UnauthorizedError."""
    with pytest.raises(UnauthorizedError) as exc_info:
        extract_bearer_token(None)
    assert exc_info.value.code == "MISSING_TOKEN"
    assert exc_info.value.status_code == 401


def test_extract_bearer_token_malformed() -> None:
    """Verify invalid format raises UnauthorizedError."""
    with pytest.raises(UnauthorizedError) as exc_info:
        extract_bearer_token("Basic dXNlcjpwYXNz")
    assert exc_info.value.code == "INVALID_HEADER_FORMAT"


def test_verify_token_valid() -> None:
    """Verify decoding a valid JWT signed with secret."""
    token = _encode({"sub": "user-uuid-1234", "role": "authenticated", "aud": "authenticated"})
    payload = verify_supabase_token(token)
    assert payload["sub"] == "user-uuid-1234"


def test_verify_token_missing_aud_rejected() -> None:
    """Tokens without 'aud': 'authenticated' are strictly rejected."""
    token = _encode({"sub": "user-uuid-1234", "role": "authenticated"})
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "INVALID_TOKEN_CLAIMS"


def test_verify_token_invalid_signature() -> None:
    """Verify token signed with wrong secret raises UnauthorizedError."""
    token = _encode({"sub": "user-uuid-1234"}, secret="wrong-secret-key-that-does-not-match-config")
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "INVALID_TOKEN"
    assert exc_info.value.status_code == 401


def test_verify_token_expired() -> None:
    """Expired tokens are rejected with TOKEN_EXPIRED."""
    token = _encode(
        {
            "sub": "user-uuid-1234",
            "aud": "authenticated",
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        }
    )
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_verify_token_missing_sub() -> None:
    """Tokens without subject claim are rejected."""
    token = _encode({"aud": "authenticated", "role": "authenticated"})
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "INVALID_TOKEN_CLAIMS"


def test_verify_token_invalid_audience() -> None:
    """Wrong audience claim is rejected."""
    token = _encode({"sub": "user-uuid-1234", "aud": "not-authenticated"})
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "INVALID_TOKEN_CLAIMS"


def test_verify_token_rejects_service_role_bearer() -> None:
    """service_role JWT must never be accepted as a user bearer credential."""
    token = _encode(
        {
            "sub": "service-user",
            "role": "service_role",
            "aud": "authenticated",
        }
    )
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_supabase_token(token)
    assert exc_info.value.code == "INVALID_TOKEN"
    assert exc_info.value.status_code == 401


def test_jwt_role_claim_is_not_application_authorization() -> None:
    """JWT may contain role=authenticated; that must not become app RBAC."""
    token = _encode(
        {
            "sub": "user-uuid-1234",
            "role": "authenticated",
            "aud": "authenticated",
            "is_super_admin": True,
            "app_role": "super_admin",
        }
    )
    payload = verify_supabase_token(token)
    # Verification succeeds, but application code must ignore these claims.
    assert payload.get("is_super_admin") is True
    assert payload.get("app_role") == "super_admin"
