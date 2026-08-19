"""Unit tests for Call Orchestrator and Context Builder services."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.gateway import AgentGateway
from app.agent.orchestrator import CallContextService, CallOrchestrator
from app.core.exceptions import ForbiddenError, NotFoundError


@pytest.mark.asyncio
async def test_call_orchestrator_blocks_dnc_customer() -> None:
    """Verify call orchestrator blocks call job creation when customer has DNC flag set."""
    mock_session = AsyncMock()
    orchestrator = CallOrchestrator(mock_session)

    orchestrator.repository.check_do_not_call = AsyncMock(return_value=True)

    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()

    mock_row = MagicMock()
    mock_row.mappings.return_value.one.return_value = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "customer_id": customer_id,
        "status": "cancelled",
        "last_error_code": "DO_NOT_CALL_ACTIVE",
    }
    mock_session.execute.return_value = mock_row

    job = await orchestrator.create_call_job(tenant_id, lead_id, customer_id)
    assert job["status"] == "cancelled"
    assert job["last_error_code"] == "DO_NOT_CALL_ACTIVE"
    orchestrator.repository.check_do_not_call.assert_called_once_with(tenant_id, customer_id)


@pytest.mark.asyncio
async def test_agent_gateway_enforces_concurrency_limit() -> None:
    """Verify start_call raises ForbiddenError when active calling count exceeds max_concurrent_calls."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session, max_concurrent_calls=2)

    tenant_id = uuid4()
    call_job_id = uuid4()

    gateway.repository.claim_job_for_update = AsyncMock(
        return_value={
            "id": call_job_id,
            "tenant_id": tenant_id,
            "status": "ready",
            "customer_id": uuid4(),
        }
    )
    gateway.repository.check_do_not_call = AsyncMock(return_value=False)
    gateway.orchestrator.is_within_calling_window = MagicMock(return_value=True)

    # Mock active calls count to equal max (2)
    mock_res = MagicMock()
    mock_res.mappings.return_value.one.return_value = {"active_calls": 2}
    mock_session.execute.return_value = mock_res

    with pytest.raises(ForbiddenError) as exc_info:
        await gateway.start_call(tenant_id, call_job_id)

    assert exc_info.value.code == "CONCURRENCY_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_call_context_service_raises_not_found_on_missing_lead() -> None:
    """Verify CallContextService raises NotFoundError when lead ID does not exist."""
    mock_session = AsyncMock()
    service = CallContextService(mock_session)
    service.repository.get_pre_call_context = AsyncMock(return_value=None)

    lead_id = uuid4()
    with pytest.raises(NotFoundError) as exc_info:
        await service.build_pre_call_context(uuid4(), lead_id)

    assert exc_info.value.code == "LEAD_NOT_FOUND"
