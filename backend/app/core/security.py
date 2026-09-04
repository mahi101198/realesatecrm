"""Security Foundation & Supabase JWT Verification.

Supports both HS256 (legacy Supabase projects) and ES256 (new Supabase
default). ES256 public keys are fetched from the Supabase JWKS endpoint at
startup and cached in-process.
"""

import logging
from typing import Any

import httpx
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

logger = logging.getLogger(__name__)

# Supabase Auth user JWTs use this audience claim.
_EXPECTED_AUDIENCE = "authenticated"
# JWT "role" claim for the privileged service key — never accepted as user bearer auth.
_SERVICE_ROLE_CLAIM = "service_role"

# Supported algorithms — HS256 (legacy) and ES256 (new Supabase default).
_SUPPORTED_ALGORITHMS = {"HS256", "ES256"}

# In-process JWKS key cache. Populated at startup via init_supabase_jwks().
# List of raw JWK dicts from the Supabase /.well-known/jwks.json endpoint.
_jwks_keys: list[dict[str, Any]] = []


async def init_supabase_jwks() -> None:
    """Fetch and cache public JWKS keys from the Supabase Auth endpoint.

    Must be called once at application startup (lifespan). Fail-open: if the
    fetch fails, ES256 tokens will be rejected with INVALID_TOKEN, but HS256
    tokens continue to work normally.
    """
    global _jwks_keys
    jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
            data = resp.json()
            _jwks_keys = data.get("keys", [])
            logger.info(
                f"Supabase JWKS loaded: {len(_jwks_keys)} public key(s) cached "
                f"(algs: {[k.get('alg') for k in _jwks_keys]})"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch Supabase JWKS from {jwks_url}: {exc!s}. "
            "ES256 tokens will be rejected until JWKS is available."
        )


def _resolve_es256_key(kid: str | None) -> dict[str, Any] | None:
    """Return the best matching JWKS public key for an ES256 token.

    Matches by `kid` when present; falls back to the first ES256 key.
    """
    if not _jwks_keys:
        return None
    # Prefer exact kid match
    if kid:
        for k in _jwks_keys:
            if k.get("kid") == kid:
                return k
    # Fall back to first ES256 key
    for k in _jwks_keys:
        if k.get("alg") == "ES256" or k.get("kty") == "EC":
            return k
    # Last resort: first available key
    return _jwks_keys[0]


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

    Supports both HS256 (legacy projects) and ES256 (new Supabase default).
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
        algorithm = unverified_header.get("alg", "HS256")

        if algorithm not in _SUPPORTED_ALGORITHMS:
            logger.warning(f"Unsupported JWT algorithm: {algorithm}")
            raise UnauthorizedError(
                message="Invalid authentication token.",
                code="INVALID_TOKEN",
            )

        if algorithm == "HS256":
            key: Any = settings.SUPABASE_JWT_SECRET.get_secret_value()
        else:
            # ES256 — use the JWKS public key fetched at startup
            kid = unverified_header.get("kid")
            key = _resolve_es256_key(kid)
            if key is None:
                logger.warning(
                    "ES256 token received but no JWKS public keys are cached. "
                    "Ensure init_supabase_jwks() ran successfully at startup."
                )
                raise UnauthorizedError(
                    message="Invalid authentication token.",
                    code="INVALID_TOKEN",
                )

        payload = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
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
