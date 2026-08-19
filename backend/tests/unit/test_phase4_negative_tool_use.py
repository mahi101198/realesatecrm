"""Negative AI tool-use tests verifying security rejection of unauthorized, malformed, or inappropriate operations."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent.tools.read_tools import get_property_details_tool
from app.agent.tools.write_tools import cancel_follow_up_tool, reschedule_site_visit_tool
from app.core.exceptions import NotFoundError
from app.core.request_context import RequestContext, SecurityScope


def _build_context(permissions: set[str]) -> RequestContext:
    return RequestContext(
        request_id="neg-test",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=uuid4(),
        role="sales_agent",
        permissions=frozenset(permissions),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


@pytest.mark.asyncio
async def test_ai_supplies_invalid_property_id() -> None:
    """Verify tool execution with non-existent property ID fails gracefully with NOT_FOUND error."""
    context = _build_context({"property.read"})
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
async def test_ai_supplies_invalid_appointment_id() -> None:
    """Verify rescheduling non-existent appointment fails with NOT_FOUND error."""
    context = _build_context({"appointment.update"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.appointments.service import AppointmentService

        mp.setattr(
            AppointmentService,
            "reschedule_appointment",
            AsyncMock(side_effect=NotFoundError(message="Appointment not found", code="APPOINTMENT_NOT_FOUND")),
        )

        res = await reschedule_site_visit_tool(
            context, session, appointment_id=uuid4(), new_scheduled_at="2026-08-22T10:00:00Z"
        )
        assert res["success"] is False
        assert res["error_code"] == "APPOINTMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_ai_supplies_unauthorized_permission_tool_call() -> None:
    """Verify AI tool call without required permission is rejected before database execution."""
    context = _build_context({"lead.read"})  # Lacks appointment.update
    session = AsyncMock()

    res = await reschedule_site_visit_tool(
        context, session, appointment_id=uuid4(), new_scheduled_at="2026-08-22T10:00:00Z"
    )
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_ai_supplies_invalid_follow_up_cancellation() -> None:
    """Verify cancelling non-existent follow-up ID returns NOT_FOUND error."""
    context = _build_context({"lead.update"})
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        from app.agent.repository import AgentRepository

        mp.setattr(
            AgentRepository,
            "cancel_follow_up",
            AsyncMock(side_effect=NotFoundError(message="Follow-up not found", code="NOT_FOUND")),
        )

        res = await cancel_follow_up_tool(context, session, follow_up_id=uuid4())
        assert res["success"] is False
        assert res["error_code"] == "NOT_FOUND"
