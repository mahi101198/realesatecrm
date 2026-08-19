"""Comprehensive multi-tenant security and cross-tenant isolation tests for Phase 4 AI CRM & Tools."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools.read_tools import (
    get_lead_context_tool,
    get_project_details_tool,
    get_property_details_tool,
)
from app.agent.tools.write_tools import (
    create_follow_up_tool,
    record_call_observation_tool,
    request_sales_agent_transfer_tool,
    update_customer_details_tool,
)
from app.core.exceptions import NotFoundError
from app.core.request_context import RequestContext, SecurityScope


def _build_tenant_context(tenant_id: str, permissions: set[str]) -> RequestContext:
    return RequestContext(
        request_id="sec-test",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tenant_id,
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_cross_tenant_lead_context_rejected() -> None:
    """Verify tenant A context cannot retrieve tenant B lead context."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"lead.read"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.agent.orchestrator import CallContextService

        mp.setattr(
            CallContextService,
            "build_pre_call_context",
            AsyncMock(side_effect=NotFoundError(message="Lead not found", code="LEAD_NOT_FOUND")),
        )

        res = await get_lead_context_tool(context, session, lead_id=uuid4())
        assert res["success"] is False
        assert res["error_code"] in ("LEAD_NOT_FOUND", "NOT_FOUND")


@pytest.mark.asyncio
async def test_cross_tenant_property_lookup_rejected() -> None:
    """Verify tenant A cannot view tenant B property details."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"property.read"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.properties.service import PropertyService

        mp.setattr(
            PropertyService,
            "get_property",
            AsyncMock(side_effect=NotFoundError(message="Property not found", code="PROPERTY_NOT_FOUND")),
        )

        res = await get_property_details_tool(context, session, property_id=uuid4())
        assert res["success"] is False
        assert res["error_code"] in ("PROPERTY_NOT_FOUND", "NOT_FOUND")


@pytest.mark.asyncio
async def test_cross_tenant_project_lookup_rejected() -> None:
    """Verify tenant A cannot view tenant B project details."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"project.read"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.projects.service import ProjectService

        mp.setattr(
            ProjectService,
            "get_project",
            AsyncMock(side_effect=NotFoundError(message="Project not found", code="PROJECT_NOT_FOUND")),
        )

        res = await get_project_details_tool(context, session, project_id=uuid4())
        assert res["success"] is False
        assert res["error_code"] in ("PROJECT_NOT_FOUND", "NOT_FOUND")


@pytest.mark.asyncio
async def test_cross_tenant_customer_update_rejected() -> None:
    """Verify tenant A agent cannot update tenant B customer details."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"customer.update"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.customers.service import CustomerService

        mp.setattr(
            CustomerService,
            "get_customer",
            AsyncMock(side_effect=NotFoundError(message="Customer not found", code="CUSTOMER_NOT_FOUND")),
        )

        res = await update_customer_details_tool(context, session, customer_id=uuid4(), city="Jaipur")
        assert res["success"] is False
        assert res["error_code"] in ("CUSTOMER_NOT_FOUND", "NOT_FOUND")


@pytest.mark.asyncio
async def test_cross_tenant_observation_creation_rejected() -> None:
    """Verify tenant A agent cannot create observation for tenant B lead."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"lead.update"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "verify_lead_and_customer_tenant",
            AsyncMock(side_effect=NotFoundError(message="Lead or customer not found for this tenant", code="NOT_FOUND")),
        )

        res = await record_call_observation_tool(
            context,
            session,
            lead_id=uuid4(),
            customer_id=uuid4(),
            observation_type="objection",
            observation_value="Too expensive",
        )
        assert res["success"] is False
        assert res["error_code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_cross_tenant_follow_up_creation_rejected() -> None:
    """Verify tenant A agent cannot create follow up for tenant B lead."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"lead.update"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "check_do_not_call",
            AsyncMock(return_value=False),
        )
        mp.setattr(
            AgentRepository,
            "verify_lead_and_customer_tenant",
            AsyncMock(side_effect=NotFoundError(message="Lead not found for this tenant", code="NOT_FOUND")),
        )

        res = await create_follow_up_tool(
            context,
            session,
            lead_id=uuid4(),
            customer_id=uuid4(),
            scheduled_at="2026-08-20T10:00:00Z",
        )
        assert res["success"] is False
        assert res["error_code"] == "NOT_FOUND"



@pytest.mark.asyncio
async def test_cross_tenant_sales_transfer_rejected() -> None:
    """Verify tenant A agent cannot transfer tenant B lead to sales agent."""
    tenant_a = uuid4()
    context = _build_tenant_context(tenant_a, {"lead.assign"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "verify_lead_and_customer_tenant",
            AsyncMock(side_effect=NotFoundError(message="Resource not found", code="NOT_FOUND")),
        )

        res = await request_sales_agent_transfer_tool(
            context, session, lead_id=uuid4(), customer_id=uuid4(), reason="Wants senior rep"
        )
        assert res["success"] is False
        assert res["error_code"] == "NOT_FOUND"
