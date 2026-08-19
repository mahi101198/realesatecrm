"""Integration tests for infrastructure health and readiness endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient) -> None:
    """Verify /api/v1/health returns 200 and liveness payload."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert "environment" in data
    assert "request_id" in data


@pytest.mark.asyncio
async def test_request_id_header(async_client: AsyncClient) -> None:
    """Verify X-Request-ID response header and correlation ID propagation."""
    custom_id = "test-custom-request-id-12345"
    response = await async_client.get("/api/v1/health", headers={"X-Request-ID": custom_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id

    data = response.json()
    assert data["request_id"] == custom_id


@pytest.mark.asyncio
async def test_readiness_endpoint(async_client: AsyncClient) -> None:
    """Verify /api/v1/ready returns status payload."""
    response = await async_client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "dependencies" in data
    assert "database" in data["dependencies"]
    assert "redis" in data["dependencies"]
