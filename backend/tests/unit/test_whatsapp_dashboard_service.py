"""Unit tests for the call-agent trigger orchestration: reason='requested'
places an immediate call (prepare_call -> start_call); anything else
queues a normal-priority call_job."""

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
    service.repository.get_or_create_lead = AsyncMock(return_value={"id": lead_id})

    with (
        patch("app.webhooks.whatsapp_dashboard.service.CallOrchestrator") as mock_orch_cls,
        patch("app.webhooks.whatsapp_dashboard.service.AgentGateway") as mock_gateway_cls,
    ):
        mock_orch_cls.return_value.create_call_job = AsyncMock(return_value={"id": job_id})
        mock_gateway_cls.return_value.prepare_call = AsyncMock(return_value={"job": {}})
        mock_gateway_cls.return_value.start_call = AsyncMock(return_value={"success": True})

        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="requested")
        )

    mock_orch_cls.return_value.create_call_job.assert_awaited_once_with(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=customer_id,
        job_type="whatsapp_callback_request",
        priority=1,
    )
    mock_gateway_cls.return_value.prepare_call.assert_awaited_once_with(tenant_id, job_id)
    mock_gateway_cls.return_value.start_call.assert_awaited_once_with(tenant_id, job_id)
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
    service.repository.get_or_create_lead = AsyncMock(return_value={"id": lead_id})

    with (
        patch("app.webhooks.whatsapp_dashboard.service.CallOrchestrator") as mock_orch_cls,
        patch(
            "app.webhooks.whatsapp_dashboard.service.AgentGateway", autospec=True
        ) as mock_gateway_cls,
    ):
        mock_orch_cls.return_value.create_call_job = AsyncMock(return_value={"id": job_id})

        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="first_message")
        )

    mock_orch_cls.return_value.create_call_job.assert_awaited_once_with(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=customer_id,
        job_type="whatsapp_callback_request",
        priority=5,
    )
    mock_gateway_cls.return_value.prepare_call.assert_not_awaited()
    mock_gateway_cls.return_value.start_call.assert_not_awaited()
    assert result["success"] is True
