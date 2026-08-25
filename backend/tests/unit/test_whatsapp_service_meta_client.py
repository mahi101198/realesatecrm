"""Unit tests verifying WhatsAppService.send_message now goes through the
per-tenant Meta client factory instead of the (deleted) Superfone client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.whatsapp.schemas import WhatsAppSendRequest
from app.whatsapp.service import WhatsAppService


@pytest.mark.asyncio
async def test_send_message_uses_tenant_scoped_meta_client() -> None:
    """Verify send_message resolves the client via get_client_for_tenant
    (tenant-scoped), not a global Superfone client, and that a successful
    send is always stored with status='sent' (Meta's synchronous response
    carries no delivery status -- that arrives later via webhook)."""
    mock_session = AsyncMock()
    # publish_event() issues its own session.execute(); an unconfigured
    # AsyncMock's attribute chain returns coroutines all the way down, so
    # `.mappings().one_or_none()` needs an explicit synchronous MagicMock.
    mock_event_result = MagicMock()
    mock_event_result.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    mock_session.execute.return_value = mock_event_result
    service = WhatsAppService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()

    service.repository.get_customer = AsyncMock(
        return_value={"id": customer_id, "phone": "+919999999999"}
    )
    service.repository.get_most_recent_inbound_message = AsyncMock(
        return_value={"created_at": datetime.now(UTC)}
    )
    service.repository.create_message = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "lead_id": None,
            "template_id": None,
            "direction": "outbound",
            "provider_message_id": "wamid.XYZ",
            "wa_id": "919999999999",
            "phone_to": "919999999999",
            "phone_from": None,
            "message_type": "text",
            "content": {"body": "hi"},
            "template_variables": {},
            "status": "sent",
            "delivered_at": None,
            "read_at": None,
            "failed_at": None,
            "failure_code": None,
            "failure_reason": None,
            "sent_at": datetime.now(UTC),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    service.repository.add_communication_log = AsyncMock(return_value=None)

    fake_client = AsyncMock()
    fake_client.send_message = AsyncMock(return_value={"message_id": "wamid.XYZ"})

    with patch(
        "app.whatsapp.service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)
    ) as mock_factory:
        await service.send_message(
            tenant_id,
            None,
            WhatsAppSendRequest(
                customer_id=customer_id, message_type="text", message={"body": "hi"}
            ),
        )

    mock_factory.assert_awaited_once_with(mock_session, tenant_id)
    # WhatsAppService.send_message calls client.send_message directly for
    # non-template types (message_type="text" here), not send_text_message
    # -- that convenience wrapper exists on MetaWhatsAppClient for other
    # internal callers but is not this call site's path.
    fake_client.send_message.assert_awaited_once()
    send_kwargs = fake_client.send_message.call_args.kwargs
    assert send_kwargs["to"] == "919999999999"
    assert send_kwargs["message_type"] == "text"
    assert send_kwargs["message"] == {"body": "hi"}
    stored_status = service.repository.create_message.call_args.kwargs["status"]
    assert stored_status == "sent"
