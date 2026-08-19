"""Unit tests for idempotency, duplicate prevention, and concurrency control in Phase 4."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.repository import AgentRepository
from app.agent.tools import dispatch_agent_tool
from app.core.exceptions import ConflictError
from app.core.request_context import RequestContext, SecurityScope


def _context_for_write() -> RequestContext:
    return RequestContext(
        request_id="idem-test",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=uuid4(),
        role="sales_agent",
        permissions=frozenset({"lead.update", "appointment.create"}),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_idempotent_tool_execution_returns_cached_payload() -> None:
    """Verify exact payload returned when repeating tool execution with same idempotency key."""
    context = _context_for_write()
    session = AsyncMock()
    key = "idem-key-999"

    cached_res = {"success": True, "data": {"follow_up_id": str(uuid4()), "status": "scheduled"}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(AgentRepository, "get_idempotent_response", AsyncMock(return_value=cached_res))

        res = await dispatch_agent_tool(
            "create_follow_up",
            context,
            session,
            {"lead_id": uuid4(), "customer_id": uuid4(), "scheduled_at": "2026-08-25T11:00:00Z"},
            idempotency_key=key,
        )

        assert res["success"] is True
        assert res["data"] == cached_res["data"]


@pytest.mark.asyncio
async def test_duplicate_follow_up_prevention_updates_existing() -> None:
    """Verify creating follow-up within 15 minutes of existing scheduled time updates existing instead of inserting duplicate."""
    session = AsyncMock()
    repo = AgentRepository(session)
    repo.verify_lead_and_customer_tenant = AsyncMock()

    existing_id = uuid4()

    # Mock dup_check returning existing row
    dup_res = MagicMock()
    dup_res.mappings.return_value.one_or_none.return_value = {"id": existing_id}

    # Mock update query returning updated row
    upd_res = MagicMock()
    upd_res.mappings.return_value.one.return_value = {
        "id": existing_id,
        "scheduled_at": "2026-08-25T11:00:00Z",
        "status": "scheduled",
    }

    session.execute.side_effect = [dup_res, upd_res]

    res = await repo.create_lead_follow_up(
        uuid4(), uuid4(), uuid4(), "2026-08-25T11:00:00Z", "ai_call", "Call back"
    )

    assert res["id"] == existing_id
    assert session.execute.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_call_job_claiming_locked_row() -> None:
    """Verify claim_job_for_update returns None when another worker holds lock (SKIP LOCKED)."""
    session = AsyncMock()
    repo = AgentRepository(session)

    mock_res = MagicMock()
    mock_res.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = mock_res

    row = await repo.claim_job_for_update(uuid4(), uuid4())
    assert row is None


@pytest.mark.asyncio
async def test_site_visit_double_booking_prevention() -> None:
    """Verify scheduling site visit on overlapping slot raises slot conflict error."""
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.appointments.service import AppointmentService

        mp.setattr(
            AppointmentService,
            "schedule_site_visit",
            AsyncMock(
                side_effect=ConflictError(
                    message="Sales agent has an appointment overlapping this slot.",
                    code="SITE_VISIT_SLOT_UNAVAILABLE",
                )
            ),
        )

        from app.agent.tools.write_tools import schedule_site_visit_tool

        res = await schedule_site_visit_tool(
            _context_for_write(),
            session,
            customer_id=uuid4(),
            scheduled_at="2026-08-20T14:00:00Z",
            sales_agent_id=uuid4(),
        )

        assert res["success"] is False
        assert res["error_code"] == "SITE_VISIT_SLOT_UNAVAILABLE"
