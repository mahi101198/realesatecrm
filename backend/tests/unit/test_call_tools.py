"""Unit tests for the call action tools -- the single code path that places a
call, and the schedule-only path that must never dial."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agent.tools import READ_TOOLS, TOOL_REGISTRY, WRITE_TOOLS
from app.agent.tools.call_tools import make_instant_call, schedule_call


def _mock_session() -> AsyncMock:
    """publish_event() issues its own session.execute(); an unconfigured
    AsyncMock's attribute chain returns coroutines all the way down, so
    `.mappings().one_or_none()` needs an explicit synchronous MagicMock."""
    session = AsyncMock()
    event_res = MagicMock()
    event_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    session.execute.return_value = event_res
    return session


@pytest.mark.asyncio
async def test_schedule_call_persists_job_and_never_dials() -> None:
    """schedule_call must contain no execution logic: it creates the job and
    stops. Anything else would give scheduled calls a second dial path."""
    session = _mock_session()
    tenant_id = uuid4()
    contact_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()
    due = datetime(2030, 1, 1, 10, 0, tzinfo=UTC)

    with (
        patch("app.agent.tools.call_tools.CallOrchestrator") as orch_cls,
        patch("app.agent.tools.call_tools.AgentGateway") as gateway_cls,
    ):
        orch_cls.return_value.create_call_job = AsyncMock(
            return_value={"id": job_id, "status": "queued"}
        )

        job = await schedule_call(
            tenant_id, contact_id, lead_id, "+919999999999", due, {}, session=session
        )

    assert job["id"] == job_id
    orch_cls.return_value.create_call_job.assert_awaited_once_with(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=contact_id,
        job_type="initial_lead_call",
        priority=5,
        scheduled_at=due,
    )
    gateway_cls.assert_not_called()


@pytest.mark.asyncio
async def test_make_instant_call_creates_job_then_dispatches() -> None:
    session = _mock_session()
    tenant_id = uuid4()
    contact_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()

    with (
        patch("app.agent.tools.call_tools.CallOrchestrator") as orch_cls,
        patch("app.agent.tools.call_tools.AgentGateway") as gateway_cls,
    ):
        orch_cls.return_value.create_call_job = AsyncMock(
            return_value={"id": job_id, "status": "queued"}
        )
        gateway_cls.return_value.prepare_call = AsyncMock(return_value={"job": {}})
        gateway_cls.return_value.start_call = AsyncMock(return_value={"success": True})

        result = await make_instant_call(
            tenant_id, contact_id, lead_id, "+919999999999", {}, session=session
        )

    gateway_cls.return_value.prepare_call.assert_awaited_once_with(tenant_id, job_id)
    gateway_cls.return_value.start_call.assert_awaited_once_with(tenant_id, job_id)
    assert result["call_job_id"] == str(job_id)


@pytest.mark.asyncio
async def test_make_instant_call_with_existing_job_does_not_create_a_second() -> None:
    """The scheduler passes an already-claimed job. Creating another row here
    would double-dial the customer."""
    session = _mock_session()
    tenant_id = uuid4()
    job_id = uuid4()

    with (
        patch("app.agent.tools.call_tools.CallOrchestrator") as orch_cls,
        patch("app.agent.tools.call_tools.AgentGateway") as gateway_cls,
    ):
        orch_cls.return_value.create_call_job = AsyncMock()
        gateway_cls.return_value.prepare_call = AsyncMock(return_value={"job": {}})
        gateway_cls.return_value.start_call = AsyncMock(return_value={"success": True})

        await make_instant_call(
            tenant_id,
            uuid4(),
            uuid4(),
            None,
            {},
            session=session,
            call_job_id=job_id,
        )

    orch_cls.return_value.create_call_job.assert_not_called()
    gateway_cls.return_value.prepare_call.assert_awaited_once_with(tenant_id, job_id)


@pytest.mark.asyncio
async def test_make_instant_call_respects_dnc_cancellation() -> None:
    """create_call_job parks a Do-Not-Call job as 'cancelled' instead of
    raising; the dispatcher must not then try to dial it anyway."""
    session = _mock_session()
    job_id = uuid4()

    with (
        patch("app.agent.tools.call_tools.CallOrchestrator") as orch_cls,
        patch("app.agent.tools.call_tools.AgentGateway") as gateway_cls,
    ):
        orch_cls.return_value.create_call_job = AsyncMock(
            return_value={
                "id": job_id,
                "status": "cancelled",
                "last_error_code": "DO_NOT_CALL_ACTIVE",
                "last_error_message": "Customer is marked as Do-Not-Call.",
            }
        )

        result = await make_instant_call(
            uuid4(), uuid4(), uuid4(), "+919999999999", {}, session=session
        )

    assert result["success"] is False
    assert result["error_code"] == "DO_NOT_CALL_ACTIVE"
    gateway_cls.return_value.prepare_call.assert_not_called()


def test_call_tools_are_registered_as_write_tools() -> None:
    """They mutate state and place real phone calls -- they must go through
    dispatch_agent_tool's idempotency + activities audit path."""
    assert TOOL_REGISTRY["make_instant_call"].__name__ == "make_instant_call_tool"
    assert TOOL_REGISTRY["schedule_call"].__name__ == "schedule_call_tool"
    assert "make_instant_call" in WRITE_TOOLS
    assert "schedule_call" in WRITE_TOOLS
    assert "make_instant_call" not in READ_TOOLS
