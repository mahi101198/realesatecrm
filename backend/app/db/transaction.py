"""Transaction Abstraction & Transaction Management Infrastructure."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@asynccontextmanager
async def atomic(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for atomic database transaction boundaries.

    Usage:
        async with atomic(session):
            await repo_a.create(...)
            await repo_b.update(...)

    Commits on successful exit, automatically rolls back on any exception.
    """
    if session.in_transaction():
        # Session is already in a transaction block, reuse nested savepoint
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            try:
                yield session
            except Exception as e:
                logger.error(f"Transaction aborted and rolled back due to error: {e!s}")
                raise
