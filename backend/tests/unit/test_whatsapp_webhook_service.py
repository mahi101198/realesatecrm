"""Unit tests for the inbound WhatsApp webhook event handlers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.webhooks.whatsapp.service import WhatsAppWebhookService


@pytest.mark.asyncio
async def test_handle_inbound_message_with_existing_customer() -> None:
    """Verify an inbound message from a known phone number is stored
    against the existing customer, no new customer created."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()

    service.repository.find_customer_by_phone = AsyncMock(
        return_value={"id": customer_id, "phone": "+919999999999"}
    )
    service.repository.create_minimal_customer = AsyncMock()
    service.repository.get_lead_for_customer = AsyncMock(return_value=None)
    service.repository.create_message = AsyncMock(return_value={"id": uuid4()})
    service.repository.add_communication_log = AsyncMock()

    event = InboundMessageEvent(
        from_phone="919999999999",
        contact_name="Jane",
        wa_message_id="wamid.ABC123",
        text="hello",
        message_type="text",
        timestamp="1700000000",
    )

    await service.handle_inbound_message(tenant_id, event)

    service.repository.create_minimal_customer.assert_not_called()
    service.repository.create_message.assert_awaited_once()
    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["customer_id"] == customer_id
    assert create_kwargs["direction"] == "inbound"
    assert create_kwargs["provider_message_id"] == "wamid.ABC123"


@pytest.mark.asyncio
async def test_handle_inbound_message_auto_creates_first_contact_customer() -> None:
    """Verify an inbound message from an unknown phone number auto-creates
    a minimal customer before storing the message."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    new_customer_id = uuid4()

    service.repository.find_customer_by_phone = AsyncMock(return_value=None)
    service.repository.create_minimal_customer = AsyncMock(
        return_value={"id": new_customer_id}
    )
    service.repository.get_lead_for_customer = AsyncMock(return_value=None)
    service.repository.create_message = AsyncMock(return_value={"id": uuid4()})
    service.repository.add_communication_log = AsyncMock()

    event = InboundMessageEvent(
        from_phone="919999999999",
        contact_name="Jane",
        wa_message_id="wamid.ABC123",
        text="hello",
        message_type="text",
        timestamp="1700000000",
    )

    await service.handle_inbound_message(tenant_id, event)

    service.repository.create_minimal_customer.assert_awaited_once_with(
        tenant_id, "+919999999999", "Jane"
    )
    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["customer_id"] == new_customer_id


@pytest.mark.asyncio
async def test_handle_status_update_known_message() -> None:
    """Verify a status callback updates the matching message and logs it."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)

    service.repository.update_message_status_by_provider_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": uuid4(),
            "customer_id": uuid4(),
            "lead_id": None,
        }
    )
    service.repository.add_communication_log = AsyncMock()

    event = StatusUpdateEvent(wa_message_id="wamid.ABC123", status="delivered")
    await service.handle_status_update(event)

    service.repository.add_communication_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_status_update_unknown_message_is_a_noop() -> None:
    """Verify a status event for a wamid this tenant never sent is skipped,
    not an error."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)

    service.repository.update_message_status_by_provider_id = AsyncMock(return_value=None)
    service.repository.add_communication_log = AsyncMock()

    event = StatusUpdateEvent(wa_message_id="wamid.UNKNOWN", status="delivered")
    await service.handle_status_update(event)

    service.repository.add_communication_log.assert_not_called()


@pytest.mark.asyncio
async def test_handle_template_status_update() -> None:
    """Verify a template approval event updates the matching template row."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    service.repository.update_template_status_by_provider_id = AsyncMock()

    event = TemplateStatusUpdateEvent(provider_template_id="123456", status="approved")
    await service.handle_template_status_update(event)

    service.repository.update_template_status_by_provider_id.assert_awaited_once_with(
        "123456", "approved", None
    )
