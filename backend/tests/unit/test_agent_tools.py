"""Unit tests for AI Agent Tools security, dispatching, and guardrails."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools import TOOL_REGISTRY, dispatch_agent_tool
from app.agent.tools.read_tools import get_lead_context_tool
from app.agent.tools.write_tools import (
    update_lead_requirements_tool,
)
from app.core.request_context import RequestContext, SecurityScope


def _context_with_permissions(
    permissions: set[str], tenant_id: str | None = None
) -> RequestContext:
    t_id = uuid4() if tenant_id is None else uuid4()
    return RequestContext(
        request_id="test-tool",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=t_id,
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_tool_registry_has_all_22_tools() -> None:
    """Verify registry contains all 22 required AI agent tools."""
    expected_tools = {
        "get_lead_context",
        "get_recent_call_summary",
        "get_open_follow_ups",
        "search_properties",
        "get_property_details",
        "get_project_details",
        "get_property_availability",
        "update_lead_requirements",
        "update_customer_details",
        "add_lead_note",
        "record_call_observation",
        "create_follow_up",
        "reschedule_follow_up",
        "cancel_follow_up",
        "check_site_visit_availability",
        "schedule_site_visit",
        "reschedule_site_visit",
        "cancel_site_visit",
        "request_sales_agent_transfer",
        "create_sales_callback",
        "record_lead_property_interest",
        "update_lead_score",
    }
    assert set(TOOL_REGISTRY.keys()) == expected_tools


@pytest.mark.asyncio
async def test_agent_tool_denies_unauthorized_permission() -> None:
    """Verify AI agent tool returns error response when lacking permission."""
    context = _context_with_permissions(permissions={"customer.read"})  # Lacks lead.read
    mock_session = AsyncMock()

    res = await get_lead_context_tool(context, mock_session, lead_id=uuid4())
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_agent_tool_denies_write_without_permission() -> None:
    """Verify AI agent tool write operation fails when lacking write permission."""
    context = _context_with_permissions(permissions={"lead.read"})  # Lacks lead.update
    mock_session = AsyncMock()

    res = await update_lead_requirements_tool(
        context, mock_session, lead_id=uuid4(), budget_max=5000000
    )
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_dispatch_agent_tool_handles_unknown_tool() -> None:
    """Verify dispatch returns UNKNOWN_TOOL error when given unrecognized tool name."""
    context = _context_with_permissions(permissions={"lead.read"})
    mock_session = AsyncMock()

    res = await dispatch_agent_tool("invalid_tool_name", context, mock_session, {})
    assert res["success"] is False
    assert res["error_code"] == "UNKNOWN_TOOL"


@pytest.mark.asyncio
async def test_dispatch_agent_tool_handles_invalid_args() -> None:
    """Verify dispatch handles invalid argument types gracefully."""
    context = _context_with_permissions(permissions={"lead.read"})
    mock_session = AsyncMock()

    # Pass unexpected keyword argument
    res = await dispatch_agent_tool(
        "get_recent_call_summary", context, mock_session, {"unexpected_arg": "value"}
    )
    assert res["success"] is False
    assert res["error_code"] == "INVALID_TOOL_ARGUMENTS"
