"""Unit tests for the inbound WhatsApp webhook event handlers."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.webhooks.whatsapp.service import WhatsAppWebhookService


def _wire_resolvers(
    service: WhatsAppWebhookService,
    contact_id: UUID,
    lead_id: UUID,
) -> None:
    """Stub the shared foundation-layer resolvers the handler delegates to."""
    service.contact_resolver.resolve_contact = AsyncMock(
        return_value={"id": contact_id, "phone": "+919999999999"}
    )
    service.lead_resolver.resolve_lead = AsyncMock(return_value={"id": lead_id})
    service.repository.create_message = AsyncMock(return_value={"id": uuid4()})
    service.repository.add_communication_log = AsyncMock()
    # _record_event (webhook_events dedup insert) and publish_event
    # (MESSAGE_RECEIVED) both call session.execute() directly; an
    # unconfigured AsyncMock's attribute chain returns coroutines all the
    # way down, so `.mappings().one_or_none()` needs an explicit synchronous
    # MagicMock returning a "row was inserted" result.
    event_res = MagicMock()
    event_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    service.session.execute.return_value = event_res


def _inbound_event() -> InboundMessageEvent:
    return InboundMessageEvent(
        from_phone="919999999999",
        contact_name="Jane",
        wa_message_id="wamid.ABC123",
        text="hello",
        message_type="text",
        timestamp="1700000000",
    )


@pytest.mark.asyncio
async def test_handle_inbound_message_delegates_contact_resolution() -> None:
    """Verify the handler no longer carries its own find-or-create: it calls
    the shared ContactResolver with the tenant from the webhook URL path and
    the normalized sender number, and stores the message against whatever
    contact that returns."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    contact_id = uuid4()
    lead_id = uuid4()
    conversation_id = uuid4()
    _wire_resolvers(service, contact_id, lead_id)

    with patch(
        "app.webhooks.whatsapp.service.get_or_create_conversation",
        AsyncMock(return_value={"id": conversation_id}),
    ):
        await service.handle_inbound_message(tenant_id, _inbound_event())

    resolve_call = service.contact_resolver.resolve_contact.call_args
    assert resolve_call.args[0] == tenant_id
    assert resolve_call.kwargs["phone"] == "+919999999999"
    assert resolve_call.kwargs["defaults"]["full_name"] == "Jane"
    assert resolve_call.kwargs["defaults"]["whatsapp_opted_in"] is True

    service.repository.create_message.assert_awaited_once()
    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["customer_id"] == contact_id
    assert create_kwargs["direction"] == "inbound"
    assert create_kwargs["provider_message_id"] == "wamid.ABC123"


@pytest.mark.asyncio
async def test_handle_inbound_message_attaches_lead_and_conversation() -> None:
    """Verify inbound messages are linked to a resolved lead AND a
    conversation. (Behaviour change: the old ad-hoc lookup left lead_id NULL
    for a first-time sender; LeadResolver opens one.)"""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    contact_id = uuid4()
    lead_id = uuid4()
    conversation_id = uuid4()
    _wire_resolvers(service, contact_id, lead_id)

    fake_get_or_create = AsyncMock(return_value={"id": conversation_id})
    with patch(
        "app.webhooks.whatsapp.service.get_or_create_conversation", fake_get_or_create
    ):
        await service.handle_inbound_message(tenant_id, _inbound_event())

    service.lead_resolver.resolve_lead.assert_awaited_once()
    assert service.lead_resolver.resolve_lead.call_args.args[0] == tenant_id
    assert service.lead_resolver.resolve_lead.call_args.args[1] == contact_id

    conv_kwargs = fake_get_or_create.call_args.kwargs
    assert conv_kwargs["tenant_id"] == tenant_id
    assert conv_kwargs["contact_id"] == contact_id
    assert conv_kwargs["lead_id"] == lead_id
    assert conv_kwargs["external_thread_id"] == "919999999999"

    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["lead_id"] == lead_id
    assert create_kwargs["conversation_id"] == conversation_id


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
