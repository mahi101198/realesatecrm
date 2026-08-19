"""SQLAlchemy 2.x Async Engine & Session Connection Pool Management."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global engine and sessionmaker instances
engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db_engine() -> AsyncEngine:
    """Initialize SQLAlchemy Async Engine with connection pool parameters."""
    global engine, async_session_factory

    db_url = settings.DATABASE_URL.get_secret_value()

    engine = create_async_engine(
        db_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_pre_ping=True,
        echo=settings.DEBUG,
    )

    async_session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    logger.info("SQLAlchemy Async Engine initialized successfully.")
    return engine


async def close_db_engine() -> None:
    """Dispose of SQLAlchemy Async Engine and connection pool."""
    global engine, async_session_factory
    if engine is not None:
        await engine.dispose()
        engine = None
        async_session_factory = None
        logger.info("SQLAlchemy Async Engine disposed.")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Dependency providing a clean, auto-closing AsyncSession.

    Rolls back automatically on unhandled exceptions and yields active session.
    """
    if async_session_factory is None:
        raise RuntimeError(
            "Database session factory is not initialized. Call init_db_engine() first."
        )

    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
