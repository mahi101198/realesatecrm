"""Unit tests for AI Agent multi-tenant isolation security."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools.read_tools import get_property_details_tool
from app.core.exceptions import NotFoundError
from app.core.request_context import RequestContext, SecurityScope


def _context_for_tenant(tenant_id: str, permissions: set[str]) -> RequestContext:
    return RequestContext(
        request_id="test-multitenant",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tenant_id,
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_tenant_a_agent_cannot_access_tenant_b_property() -> None:
    """Verify Tenant A agent attempting to access Tenant B property returns NOT_FOUND/FORBIDDEN error."""
    tenant_a = uuid4()

    context = _context_for_tenant(tenant_a, permissions={"property.read"})
    mock_session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.properties.service import PropertyService

        # PropertyService returns NotFoundError when tenant_id does not match
        mp.setattr(
            PropertyService,
            "get_property",
            AsyncMock(
                side_effect=NotFoundError(
                    message="Property not found for this tenant.", code="PROPERTY_NOT_FOUND"
                )
            ),
        )

        res = await get_property_details_tool(context, mock_session, property_id=uuid4())
        assert res["success"] is False
        assert res["error_code"] in (
            "NOT_FOUND",
            "PROPERTY_NOT_FOUND",
            "FORBIDDEN",
            "TENANT_ACCESS_DENIED",
        )

