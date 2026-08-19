"""Redis Ephemeral Connection Manager."""

import logging

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client: redis.Redis | None = None


async def init_redis() -> redis.Redis | None:
    """Initialize ephemeral Redis client connection."""
    global redis_client
    try:
        redis_url = settings.REDIS_URL.get_secret_value()
        redis_client = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis successfully.")
        return redis_client
    except Exception as e:
        logger.warning(
            f"Initial Redis connection ping failed: {e!s}. Application will retry on demand."
        )
        return redis_client


async def close_redis() -> None:
    """Close Redis client connection on application shutdown."""
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None
        logger.info("Redis client connection closed.")


def get_redis_client() -> redis.Redis | None:
    """Return active Redis client instance."""
    return redis_client
