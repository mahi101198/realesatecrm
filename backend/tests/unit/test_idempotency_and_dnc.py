"""Unit tests for tool idempotency keys, duplicate follow-up prevention, and DNC enforcement."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools import dispatch_agent_tool
from app.core.request_context import RequestContext, SecurityScope


def _context_with_permissions(permissions: set[str]) -> RequestContext:
    return RequestContext(
        request_id="test-idempotency",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=uuid4(),
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_idempotent_tool_execution_returns_cached_payload() -> None:
    """Verify tool dispatch returns cached response when idempotency_key is repeated."""
    context = _context_with_permissions(permissions={"lead.update"})
    mock_session = AsyncMock()

    idempotency_key = "uniq-key-123"

    # Mock repository get_idempotent_response to return cached result
    cached_payload = {
        "success": True,
        "data": {"follow_up_id": str(uuid4()), "status": "scheduled"},
    }

    # Patch repository method
    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "get_idempotent_response",
            AsyncMock(return_value=cached_payload),
        )

        res = await dispatch_agent_tool(
            "create_follow_up",
            context,
            mock_session,
            {"lead_id": uuid4(), "customer_id": uuid4(), "scheduled_at": "2026-08-15T10:00:00Z"},
            idempotency_key=idempotency_key,
        )

        assert res["success"] is True
        assert res == cached_payload
