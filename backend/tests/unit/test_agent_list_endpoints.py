"""Unit tests for the dashboard/reporting list endpoints added to the agent
module: GET /agent/calls and GET /agent/sales-handoffs. Neither existed
before -- the backend was built agent-first (webhooks, tool execution), with
no admin-facing read surface for call history or handoff queues."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.gateway import AgentGateway
from app.agent.handoff_service import SalesHandoffService
from app.agent.repository import AgentRepository
from app.agent.schemas import CallFilter, SalesHandoffFilter
from app.shared.schemas import PaginationParams


def _mock_search_result(rows: list[dict], total: int) -> MagicMock:
    count_res = MagicMock()
    count_res.scalar_one.return_value = total
    select_res = MagicMock()
    select_res.mappings.return_value.all.return_value = rows
    return count_res, select_res


@pytest.mark.asyncio
async def test_search_calls_applies_all_filters_and_tenant_scope() -> None:
    """Verify every CallFilter field, plus tenant_id, ends up as a bound
    parameter -- not silently dropped."""
    session = AsyncMock()
    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()
    count_res, select_res = _mock_search_result([], 0)
    session.execute.side_effect = [count_res, select_res]

    repo = AgentRepository(session)
    await repo.search_calls(
        tenant_id,
        CallFilter(
            lead_id=lead_id,
            customer_id=customer_id,
            status="completed",
            outcome="connected",
        ),
        PaginationParams(page=1, page_size=25),
    )

    count_sql, count_params = session.execute.await_args_list[0].args
    assert "public.calls" in str(count_sql)
    assert count_params["tenant_id"] == tenant_id
    assert count_params["filter_lead_id"] == lead_id
    assert count_params["filter_customer_id"] == customer_id
    assert count_params["filter_status"] == "completed"
    assert count_params["filter_outcome"] == "connected"


@pytest.mark.asyncio
async def test_search_calls_returns_rows_and_total() -> None:
    session = AsyncMock()
    call_id = uuid4()
    rows = [{"id": call_id, "status": "completed"}]
    count_res, select_res = _mock_search_result(rows, 1)
    session.execute.side_effect = [count_res, select_res]

    repo = AgentRepository(session)
    result_rows, total = await repo.search_calls(
        uuid4(), CallFilter(), PaginationParams(page=1, page_size=25)
    )

    assert total == 1
    assert result_rows == rows


@pytest.mark.asyncio
async def test_agent_gateway_list_calls_wraps_into_paginated_response() -> None:
    """Verify AgentGateway.list_calls computes `pages` correctly and never
    touches the repository's raw SQL directly (thin wrapper only)."""
    session = AsyncMock()
    gateway = AgentGateway(session)
    tenant_id = uuid4()
    customer_id = uuid4()

    rows = [
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "lead_id": None,
            "customer_id": customer_id,
            "direction": "outbound",
            "provider": "superfone_sfvopi",
            "phone_from": "+919900000000",
            "phone_to": "+919911111111",
            "status": "completed",
            "outcome": "connected",
            "initiated_at": "2026-08-20T10:00:00+00:00",
            "answered_at": "2026-08-20T10:00:05+00:00",
            "ended_at": "2026-08-20T10:03:00+00:00",
            "duration_seconds": 175,
            "recording_url": None,
            "call_summary": "Customer asked for a 2BHK.",
            "created_at": "2026-08-20T10:00:00+00:00",
        }
    ]
    gateway.repository.search_calls = AsyncMock(return_value=(rows, 37))

    result = await gateway.list_calls(
        tenant_id, CallFilter(), PaginationParams(page=2, page_size=10)
    )

    assert result.total == 37
    assert result.page == 2
    assert result.page_size == 10
    assert result.pages == 4  # ceil(37 / 10)
    assert len(result.items) == 1
    assert result.items[0].outcome == "connected"


@pytest.mark.asyncio
async def test_search_sales_handoffs_applies_all_filters_and_tenant_scope() -> None:
    session = AsyncMock()
    tenant_id = uuid4()
    lead_id = uuid4()
    assigned_user_id = uuid4()
    count_res, select_res = _mock_search_result([], 0)
    session.execute.side_effect = [count_res, select_res]

    repo = AgentRepository(session)
    await repo.search_sales_handoffs(
        tenant_id,
        SalesHandoffFilter(lead_id=lead_id, status="requested", assigned_user_id=assigned_user_id),
        PaginationParams(page=1, page_size=25),
    )

    count_sql, count_params = session.execute.await_args_list[0].args
    assert "public.sales_handoffs" in str(count_sql)
    assert count_params["tenant_id"] == tenant_id
    assert count_params["filter_lead_id"] == lead_id
    assert count_params["filter_status"] == "requested"
    assert count_params["filter_assigned_user_id"] == assigned_user_id


@pytest.mark.asyncio
async def test_sales_handoff_service_list_handoffs_wraps_into_paginated_response() -> None:
    session = AsyncMock()
    service = SalesHandoffService(session)
    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()

    rows = [
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "customer_id": customer_id,
            "reason": "Customer wants a price negotiation beyond AI authority.",
            "priority": 5,
            "status": "requested",
            "assigned_user_id": None,
            "requested_at": "2026-08-20T10:00:00+00:00",
            "accepted_at": None,
            "completed_at": None,
            "notes": None,
            "created_at": "2026-08-20T10:00:00+00:00",
        }
    ]
    service.repository.search_sales_handoffs = AsyncMock(return_value=(rows, 3))

    result = await service.list_handoffs(
        tenant_id, SalesHandoffFilter(), PaginationParams(page=1, page_size=25)
    )

    assert result.total == 3
    assert result.pages == 1
    assert len(result.items) == 1
    assert result.items[0].status == "requested"
