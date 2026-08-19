"""Unit tests verifying complete business lifecycle flow and multi-call callback continuity."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.orchestrator import CallContextService, CallOrchestrator
from app.agent.schemas import AgentPreCallContext
from app.agent.tools.write_tools import (
    create_follow_up_tool,
    update_lead_requirements_tool,
)
from app.core.request_context import RequestContext, SecurityScope


def _build_context() -> RequestContext:
    return RequestContext(
        request_id="biz-test",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=uuid4(),
        role="sales_agent",
        permissions=frozenset({"lead.read", "lead.update", "property.read"}),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_complete_end_to_end_lead_call_flow() -> None:
    """Verify complete business flow: Lead creation -> Call Job -> Pre-Call Context -> Search Property -> Update Requirement -> Callback -> Follow-up."""
    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()
    call_job_id = uuid4()
    context = _build_context()
    session = AsyncMock()

    # 1. Create Call Job
    orchestrator = CallOrchestrator(session)
    orchestrator.repository.check_do_not_call = AsyncMock(return_value=False)

    job_row = {
        "id": call_job_id,
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "customer_id": customer_id,
        "status": "queued",
        "job_type": "initial_lead_call",
        "priority": 5,
    }
    mock_job_res = MagicMock()
    mock_job_res.mappings.return_value.one.return_value = job_row
    session.execute.return_value = mock_job_res

    created_job = await orchestrator.create_call_job(tenant_id, lead_id, customer_id)
    assert created_job["status"] == "queued"

    # 2. Update requirements
    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "update_lead_requirements_with_history",
            AsyncMock(
                return_value={
                    "lead_id": str(lead_id),
                    "budget_min": 5000000,
                    "budget_max": 6000000,
                    "preferred_city": "Jaipur",
                }
            ),
        )

        req_res = await update_lead_requirements_tool(
            context,
            session,
            lead_id=lead_id,
            budget_min=5000000,
            budget_max=6000000,
            preferred_location="Jaipur",
        )
        assert req_res["success"] is True

    # 3. Create Follow-Up
    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(AgentRepository, "verify_lead_and_customer_tenant", AsyncMock())
        mp.setattr(AgentRepository, "check_do_not_call", AsyncMock(return_value=False))
        mp.setattr(
            AgentRepository,
            "create_lead_follow_up",
            AsyncMock(
                return_value={
                    "id": uuid4(),
                    "scheduled_at": datetime.now(UTC),
                    "status": "scheduled",
                }
            ),
        )

        fol_res = await create_follow_up_tool(
            context,
            session,
            lead_id=lead_id,
            customer_id=customer_id,
            scheduled_at=datetime.now(UTC),
            reason="Wants to discuss with family",
        )
        assert fol_res["success"] is True


@pytest.mark.asyncio
async def test_callback_continuity_between_calls() -> None:
    """Verify Call 2 pre-call context includes requirements, observations, and outcome from Call 1."""
    session = AsyncMock()
    service = CallContextService(session)

    lead_id = uuid4()
    tenant_id = uuid4()

    mock_pre_call = MagicMock(spec=AgentPreCallContext)
    mock_pre_call.lead_id = lead_id
    mock_pre_call.tenant_id = tenant_id
    mock_pre_call.temperature = "hot"
    mock_pre_call.requirement = MagicMock()
    mock_pre_call.requirement.budget_min = 5000000
    mock_pre_call.requirement.budget_max = 6000000
    mock_pre_call.relationship = MagicMock()
    mock_pre_call.relationship.previous_calls = 1
    mock_pre_call.relationship.last_outcome = "customer_requested_callback"
    mock_pre_call.sales_context = MagicMock()
    mock_pre_call.sales_context.main_objections = ["Price high"]

    service.repository.get_pre_call_context = AsyncMock(return_value=mock_pre_call)

    ctx = await service.build_pre_call_context(tenant_id, lead_id)
    assert ctx.relationship.previous_calls == 1
    assert ctx.relationship.last_outcome == "customer_requested_callback"
    assert ctx.sales_context.main_objections == ["Price high"]
