"""Unit tests for the two new AI agent write tools: property interest + lead score."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools import TOOL_REGISTRY, WRITE_TOOLS, dispatch_agent_tool
from app.agent.tools.write_tools import (
    record_lead_property_interest_tool,
    update_lead_score_tool,
)
from app.core.request_context import RequestContext, SecurityScope


def _context_with_permissions(permissions: set[str]) -> RequestContext:
    return RequestContext(
        request_id="test-new-tool",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=uuid4(),
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


def test_new_tools_registered_as_write_tools() -> None:
    """Verify both new tools are registered and classified as WRITE tools (audited)."""
    assert "record_lead_property_interest" in TOOL_REGISTRY
    assert "update_lead_score" in TOOL_REGISTRY
    assert "record_lead_property_interest" in WRITE_TOOLS
    assert "update_lead_score" in WRITE_TOOLS


@pytest.mark.asyncio
async def test_record_lead_property_interest_denies_without_permission() -> None:
    """Verify record_lead_property_interest_tool requires lead.update permission."""
    context = _context_with_permissions({"lead.read"})
    session = AsyncMock()

    res = await record_lead_property_interest_tool(
        context, session, lead_id=uuid4(), project_id=uuid4()
    )
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_record_lead_property_interest_rejects_invalid_interest_level() -> None:
    """Verify an invalid interest_level (not one of the DB's public.interest_level
    enum members) is rejected with a clean, specific error BEFORE ever reaching
    the DB -- never a raw DataError bubbling up as a generic INTERNAL_ERROR."""
    context = _context_with_permissions({"lead.update"})
    session = AsyncMock()

    res = await record_lead_property_interest_tool(
        context, session, lead_id=uuid4(), project_id=uuid4(), interest_level="super_high"
    )
    assert res["success"] is False
    assert res["error_code"] == "INVALID_INTEREST_LEVEL"


@pytest.mark.asyncio
async def test_update_lead_score_denies_without_permission() -> None:
    """Verify update_lead_score_tool requires lead.update permission."""
    context = _context_with_permissions({"lead.read"})
    session = AsyncMock()

    res = await update_lead_score_tool(context, session, lead_id=uuid4(), lead_score=80)
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_update_lead_score_rejects_out_of_bounds_score() -> None:
    """Verify update_lead_score_tool rejects scores outside 0-100 before hitting the DB."""
    context = _context_with_permissions({"lead.read", "lead.update"})
    session = AsyncMock()

    res = await update_lead_score_tool(context, session, lead_id=uuid4(), lead_score=150)
    assert res["success"] is False
    assert res["error_code"] == "INVALID_LEAD_SCORE"


@pytest.mark.asyncio
async def test_update_lead_score_is_idempotent_across_repeated_calls() -> None:
    """Verify setting the same lead_score twice produces the same success payload."""
    from app.leads.schemas import LeadResponse
    from app.leads.service import LeadService

    context = _context_with_permissions({"lead.read", "lead.update"})
    session = AsyncMock()
    lead_id = uuid4()

    lead_row = LeadResponse.model_construct(
        id=lead_id,
        tenant_id=context.tenant_id,
        customer_id=uuid4(),
        lead_number="LD-000001",
        area_unit="sqft",
        status="new",
        sales_stage="new",
        lead_score=80,
        metadata={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(LeadService, "get_lead", AsyncMock(return_value=lead_row))
        mp.setattr(
            LeadService,
            "update_lead",
            AsyncMock(return_value=lead_row.model_copy(update={"lead_score": 80})),
        )

        res1 = await update_lead_score_tool(context, session, lead_id=lead_id, lead_score=80)
        res2 = await update_lead_score_tool(context, session, lead_id=lead_id, lead_score=80)

        assert res1 == res2
        assert res1["success"] is True
        assert res1["data"]["lead_score"] == 80


@pytest.mark.asyncio
async def test_dispatch_record_lead_property_interest_via_registry() -> None:
    """Verify dispatch_agent_tool routes to record_lead_property_interest_tool by name."""
    from app.leads.schemas import LeadPropertyInterestResponse, LeadResponse
    from app.leads.service import LeadService

    context = _context_with_permissions({"lead.update"})
    session = AsyncMock()
    lead_id = uuid4()
    project_id = uuid4()

    lead_row = LeadResponse.model_construct(
        id=lead_id,
        tenant_id=context.tenant_id,
        customer_id=uuid4(),
        lead_number="LD-000001",
        area_unit="sqft",
        status="new",
        sales_stage="new",
        lead_score=0,
        metadata={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    interest_row = LeadPropertyInterestResponse.model_construct(
        id=uuid4(),
        tenant_id=context.tenant_id,
        lead_id=lead_id,
        project_id=project_id,
        property_id=None,
        interest_level="medium",
        is_primary=False,
        notes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(LeadService, "get_lead", AsyncMock(return_value=lead_row))
        mp.setattr(LeadService, "add_property_interest", AsyncMock(return_value=interest_row))

        res = await dispatch_agent_tool(
            "record_lead_property_interest",
            context,
            session,
            {"lead_id": lead_id, "project_id": project_id},
        )

        assert res["success"] is True
        assert res["data"]["project_id"] == str(project_id)
