"""Security tests for Phase 3 CRM endpoints and tenant isolation."""

from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_crm_endpoints_require_authentication(async_client: AsyncClient) -> None:
    """Verify all CRM REST endpoints return 401 when accessed without Bearer token."""
    endpoints = [
        ("GET", "/api/v1/customers"),
        ("POST", "/api/v1/customers"),
        ("GET", f"/api/v1/customers/{uuid4()}"),
        ("PATCH", f"/api/v1/customers/{uuid4()}"),
        ("GET", "/api/v1/leads"),
        ("POST", "/api/v1/leads"),
        ("GET", f"/api/v1/leads/{uuid4()}"),
        ("PATCH", f"/api/v1/leads/{uuid4()}"),
        ("POST", f"/api/v1/leads/{uuid4()}/assign"),
        ("GET", "/api/v1/projects"),
        ("GET", f"/api/v1/projects/{uuid4()}"),
        ("GET", "/api/v1/properties"),
        ("GET", f"/api/v1/properties/{uuid4()}"),
        ("POST", f"/api/v1/properties/{uuid4()}/reserve"),
        ("GET", "/api/v1/appointments"),
        ("POST", "/api/v1/appointments"),
        ("GET", f"/api/v1/appointments/{uuid4()}"),
        ("PATCH", f"/api/v1/appointments/{uuid4()}"),
        ("POST", f"/api/v1/appointments/{uuid4()}/cancel"),
        ("GET", "/api/v1/followups"),
        ("POST", "/api/v1/followups"),
        ("GET", f"/api/v1/followups/{uuid4()}"),
        ("PATCH", f"/api/v1/followups/{uuid4()}"),
        ("POST", f"/api/v1/followups/{uuid4()}/complete"),
        ("GET", f"/api/v1/agent/context/{uuid4()}"),
    ]

    for method, path in endpoints:
        if method == "GET":
            response = await async_client.get(path)
        elif method == "POST":
            response = await async_client.post(path, json={})
        elif method == "PATCH":
            response = await async_client.patch(path, json={})

        assert response.status_code == 401, (
            f"Expected 401 for {method} {path}, got {response.status_code}"
        )
        assert response.json()["error"]["code"] == "MISSING_TOKEN"
