"""Pytest Test Configuration and Fixtures."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Explicitly ensure tests run in test environment
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-role-key-secret"
os.environ["SUPABASE_JWT_SECRET"] = "test-jwt-secret-key-super-secret-minimum-32-chars"

from app.main import app


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Configure anyio backend for pytest-asyncio."""
    return "asyncio"


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient bound to the FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
