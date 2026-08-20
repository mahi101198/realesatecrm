"""Inbound WhatsApp webhook event handlers -- persist inbound messages,
delivery-status updates, and template status updates. No auto-reply logic
in this phase (spec Non-Goals)."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.whatsapp.repository import WhatsAppRepository

logger = logging.getLogger(__name__)


class WhatsAppWebhookService:
    """Processes parsed WhatsApp webhook events for one tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WhatsAppRepository(session)

    async def handle_inbound_message(self, tenant_id: UUID, event: InboundMessageEvent) -> None:
        """Resolve/auto-create the customer, persist the message and a
        communication_logs entry. No AI reply is generated."""
        phone = f"+{event.from_phone}"
        customer = await self.repository.find_customer_by_phone(tenant_id, phone)
        if not customer:
            customer = await self.repository.create_minimal_customer(
                tenant_id, phone, event.contact_name or phone
            )

        lead = await self.repository.get_lead_for_customer(tenant_id, customer["id"])
        lead_id = lead["id"] if lead else None

        message_row = await self.repository.create_message(
            tenant_id=tenant_id,
            customer_id=customer["id"],
            lead_id=lead_id,
            direction="inbound",
            provider_message_id=event.wa_message_id,
            wa_id=event.from_phone,
            phone_to=None,
            phone_from=event.from_phone,
            message_type=event.message_type,
            content={"body": event.text} if event.text else {},
            template_variables={},
            status="delivered",
            sent_at=None,
        )

        await self.repository.add_communication_log(
            tenant_id=tenant_id,
            customer_id=customer["id"],
            lead_id=lead_id,
            whatsapp_message_id=message_row["id"],
            direction="inbound",
            status="delivered",
            summary="WhatsApp message received",
            initiated_by="system",
            initiated_by_id=None,
        )

    async def handle_status_update(self, event: StatusUpdateEvent) -> None:
        """Update the matching message's delivery status. A status event
        for a wamid this tenant never sent through this integration is
        skipped, not an error (e.g. a message sent before this pipeline
        existed)."""
        updated = await self.repository.update_message_status_by_provider_id(
            event.wa_message_id,
            event.status,
            delivered_at=None,
            read_at=None,
            failed_at=None,
            failure_code=None,
            failure_reason=event.error_message,
        )
        if not updated:
            logger.info(
                f"whatsapp webhook: status update for unknown wamid "
                f"{event.wa_message_id!r}, skipping"
            )
            return

        await self.repository.add_communication_log(
            tenant_id=updated["tenant_id"],
            customer_id=updated["customer_id"],
            lead_id=updated["lead_id"],
            whatsapp_message_id=updated["id"],
            direction="outbound",
            status=event.status,
            summary=f"WhatsApp message {event.status}",
            initiated_by="system",
            initiated_by_id=None,
        )

    async def handle_template_status_update(self, event: TemplateStatusUpdateEvent) -> None:
        """Update the matching template's approval status."""
        await self.repository.update_template_status_by_provider_id(
            event.provider_template_id, event.status, event.rejection_reason
        )
