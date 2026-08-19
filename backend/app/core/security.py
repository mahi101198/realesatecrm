"""Security Foundation & Supabase JWT Verification."""

import logging
from typing import Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

logger = logging.getLogger(__name__)

# Supabase Auth user JWTs use this audience claim.
_EXPECTED_AUDIENCE = "authenticated"
# JWT "role" claim for the privileged service key — never accepted as user bearer auth.
_SERVICE_ROLE_CLAIM = "service_role"


def extract_bearer_token(authorization_header: str | None) -> str:
    """Extract raw bearer token string from HTTP Authorization header."""
    if not authorization_header:
        raise UnauthorizedError(
            message="Authorization header is missing.",
            code="MISSING_TOKEN",
        )

    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise UnauthorizedError(
            message="Invalid Authorization header format. Expected 'Bearer <token>'.",
            code="INVALID_HEADER_FORMAT",
        )

    return parts[1]


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Cryptographically verify a Supabase Auth JWT.

    Validates signature, expiration, audience (when present), and subject.
    Does NOT trust application role, tenant, or permission claims from the token.
    Rejects service_role tokens used as user bearer credentials.
    """
    jwt_secret = settings.SUPABASE_JWT_SECRET.get_secret_value()

    try:
        unverified_header = jwt.get_unverified_header(token)
        algorithm = unverified_header.get("alg", "HS256")
        if algorithm != "HS256":
            raise UnauthorizedError(
                message="Invalid authentication token.",
                code="INVALID_TOKEN",
            )

        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": False,
            },
        )
    except UnauthorizedError:
        raise
    except ExpiredSignatureError as e:
        raise UnauthorizedError(
            message="Authentication token has expired.",
            code="TOKEN_EXPIRED",
        ) from e
    except (JWTError, ValueError, KeyError) as e:
        logger.warning("JWT verification failed", extra={"failure_category": "INVALID_TOKEN"})
        raise UnauthorizedError(
            message="Invalid authentication token.",
            code="INVALID_TOKEN",
        ) from e

    # Never accept the privileged service-role JWT as a user API credential.
    if payload.get("role") == _SERVICE_ROLE_CLAIM:
        logger.warning(
            "Rejected service_role token used as user bearer",
            extra={"failure_category": "SERVICE_ROLE_BEARER_REJECTED"},
        )
        raise UnauthorizedError(
            message="Invalid authentication token.",
            code="INVALID_TOKEN",
        )

    audience = payload.get("aud")
    if audience != _EXPECTED_AUDIENCE:
        logger.warning(
            "JWT audience mismatch or missing",
            extra={"failure_category": "INVALID_TOKEN_CLAIMS"},
        )
        raise UnauthorizedError(
            message="Invalid authentication token.",
            code="INVALID_TOKEN_CLAIMS",
        )

    subject = payload.get("sub")
    if not subject or not isinstance(subject, str):
        raise UnauthorizedError(
            message="Invalid authentication token.",
            code="INVALID_TOKEN_CLAIMS",
        )

    return payload
