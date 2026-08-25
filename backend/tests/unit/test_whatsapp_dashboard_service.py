"""Unit tests for the call-agent trigger orchestration: reason='requested'
places an immediate call via make_instant_call; anything else queues a
normal-priority call_job via schedule_call, which never dials."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest
from app.webhooks.whatsapp_dashboard.service import CallAgentTriggerService


@pytest.mark.asyncio
async def test_unattributable_phone_returns_customer_not_found() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(return_value=None)

    result = await service.trigger(
        CallAgentTriggerRequest(phone="+919999999999", reason="requested")
    )

    assert result == {"success": False, "error_code": "CUSTOMER_NOT_FOUND"}


@pytest.mark.asyncio
async def test_reason_requested_places_immediate_call() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()

    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(
        return_value={"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    )
    service.lead_resolver.resolve_lead = AsyncMock(return_value={"id": lead_id})

    fake_instant = AsyncMock(return_value={"success": True, "call_job_id": str(job_id)})
    fake_schedule = AsyncMock()
    with (
        patch("app.webhooks.whatsapp_dashboard.service.make_instant_call", fake_instant),
        patch("app.webhooks.whatsapp_dashboard.service.schedule_call", fake_schedule),
    ):
        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="requested")
        )

    fake_schedule.assert_not_called()
    fake_instant.assert_awaited_once()
    args = fake_instant.call_args.args
    assert args[0] == tenant_id
    assert args[1] == customer_id
    assert args[2] == lead_id
    assert args[4]["priority"] == 1
    assert args[4]["job_type"] == "whatsapp_callback_request"
    assert fake_instant.call_args.kwargs["session"] is mock_session
    assert result["success"] is True


@pytest.mark.asyncio
async def test_other_reason_queues_call_job_without_placing_call() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()

    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(
        return_value={"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    )
    service.lead_resolver.resolve_lead = AsyncMock(return_value={"id": lead_id})

    fake_instant = AsyncMock()
    fake_schedule = AsyncMock(return_value={"id": job_id})
    with (
        patch("app.webhooks.whatsapp_dashboard.service.make_instant_call", fake_instant),
        patch("app.webhooks.whatsapp_dashboard.service.schedule_call", fake_schedule),
    ):
        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="first_message")
        )

    fake_instant.assert_not_called()
    fake_schedule.assert_awaited_once()
    args = fake_schedule.call_args.args
    assert args[0] == tenant_id
    assert args[1] == customer_id
    assert args[2] == lead_id
    assert args[4] is None  # scheduled_at: due immediately, dispatcher decides
    assert args[5]["priority"] == 5
    assert result == {"success": True, "data": {"call_job_id": str(job_id), "queued": True}}


@pytest.mark.asyncio
async def test_tenant_comes_from_matched_customer_never_the_request() -> None:
    """The request body carries no tenant; the tenant used for the call must
    come from the matched customer row."""
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()

    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(
        return_value={"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    )
    service.lead_resolver.resolve_lead = AsyncMock(return_value={"id": uuid4()})

    with (
        patch(
            "app.webhooks.whatsapp_dashboard.service.make_instant_call",
            AsyncMock(return_value={"success": True}),
        ),
        patch("app.webhooks.whatsapp_dashboard.service.schedule_call", AsyncMock()),
    ):
        await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="requested")
        )

    assert service.lead_resolver.resolve_lead.call_args.args[0] == tenant_id
