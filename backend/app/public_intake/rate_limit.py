"""Redis-backed rate limiting for the public lead intake endpoint.

This endpoint has no auth/permission gate at all (by design -- it is meant
for anonymous website/campaign traffic), so it is the one place in this
codebase where rate limiting is a hard security control rather than a nice-
to-have. Uses app/db/redis.py's ephemeral client with a simple fixed-window
INCR+EXPIRE counter, keyed per (tenant, client IP).
"""

import logging

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "public_intake_rl"
_WINDOW_SECONDS = 60


class RateLimitExceededError(AppError):
    """Raised when a client exceeds the public intake rate limit."""

    def __init__(self) -> None:
        super().__init__(
            message="Too many requests. Please try again in a minute.",
            code="RATE_LIMIT_EXCEEDED",
            status_code=429,
        )


class RateLimiterUnavailableError(AppError):
    """Raised when Redis is unreachable. Fails CLOSED: for an unauthenticated,
    permission-free write endpoint, the rate limiter IS the abuse-prevention
    control, so "rate limiter down" must not silently become "no limit"."""

    def __init__(self) -> None:
        super().__init__(
            message="This service is temporarily unavailable. Please try again shortly.",
            code="RATE_LIMITER_UNAVAILABLE",
            status_code=503,
        )


async def enforce_public_intake_rate_limit(tenant_slug: str, client_ip: str) -> None:
    """Enforce the per-tenant-per-IP rate limit for public lead intake.

    Raises RateLimitExceededError (429) if the limit is exceeded, or
    RateLimiterUnavailableError (503) if Redis itself is unreachable --
    fail-closed, since this is the only abuse guard on an unauthenticated
    write endpoint.
    """
    redis_client = get_redis_client()
    if redis_client is None:
        logger.error("Public intake rate limiter unavailable: Redis client not connected.")
        raise RateLimiterUnavailableError

    key = f"{_KEY_PREFIX}:{tenant_slug}:{client_ip}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, _WINDOW_SECONDS)
    except Exception as e:
        logger.error(f"Public intake rate limiter Redis error: {e!s}")
        raise RateLimiterUnavailableError from e

    if count > settings.PUBLIC_INTAKE_RATE_LIMIT_PER_MINUTE:
        logger.warning(
            f"Public intake rate limit exceeded for tenant='{tenant_slug}' ip='{client_ip}'."
        )
        raise RateLimitExceededError
