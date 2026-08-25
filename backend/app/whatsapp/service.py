"""WhatsApp Messaging Business Service Layer."""

import logging
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.events.model import EventType
from app.events.publisher import publish_event
from app.integrations.whatsapp.factory import get_client_for_tenant
from app.shared.schemas import PaginatedResponse, PaginationParams
from app.whatsapp.repository import WhatsAppRepository
from app.whatsapp.schemas import (
    WhatsAppMessageFilter,
    WhatsAppMessageResponse,
    WhatsAppSendRequest,
    WhatsAppTemplateResponse,
)

logger = logging.getLogger(__name__)

_SESSION_WINDOW_HOURS = 24


class WhatsAppService:
    """Service for sending/listing WhatsApp messages and listing templates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WhatsAppRepository(session)

    async def send_message(
        self, tenant_id: UUID, created_by: UUID | None, data: WhatsAppSendRequest
    ) -> WhatsAppMessageResponse:
        """Send a WhatsApp message, enforcing the 24-hour session-window
        rule for non-template message types before ever calling Meta."""
        customer = await self.repository.get_customer(tenant_id, data.customer_id)
        if not customer:
            raise NotFoundError(
                message=f"Customer with ID '{data.customer_id}' was not found in this tenant.",
                code="CUSTOMER_NOT_FOUND",
            )
        if data.lead_id is not None:
            lead = await self.repository.get_lead(tenant_id, data.lead_id)
            if not lead:
                raise NotFoundError(
                    message=f"Lead with ID '{data.lead_id}' was not found in this tenant.",
                    code="LEAD_NOT_FOUND",
                )

        if data.message_type != "template":
            await self._enforce_session_window(tenant_id, data.customer_id)

        client = await get_client_for_tenant(self.session, tenant_id)
        recipient = _to_whatsapp_recipient(customer["phone"])

        if data.message_type == "template":
            template_name = data.template_name
            language = data.language
            if not template_name or not language:
                # Unreachable in practice -- WhatsAppSendRequest's own
                # validator already guarantees this -- but never trust a
                # narrowing assumption silently; fail with a clear error
                # instead of an assert that vanishes under -O.
                raise BusinessRuleError(
                    message="template_name and language are required for type=template.",
                    code="WHATSAPP_TEMPLATE_FIELDS_MISSING",
                )
            result = await client.send_template_message(
                to=recipient,
                template_name=template_name,
                language=language,
                components=data.components,
                context_message_id=data.context_message_id,
            )
            content: dict[str, Any] = {
                "templateName": template_name,
                "language": language,
                "components": data.components,
            }
        else:
            message_body = data.message
            if not message_body:
                raise BusinessRuleError(
                    message=f"message is required for type={data.message_type}.",
                    code="WHATSAPP_MESSAGE_BODY_MISSING",
                )
            result = await client.send_message(
                to=recipient,
                message_type=data.message_type,
                message=message_body,
                context_message_id=data.context_message_id,
            )
            content = dict(message_body)

        db_status = "sent"

        row = await self.repository.create_message(
            tenant_id=tenant_id,
            customer_id=data.customer_id,
            lead_id=data.lead_id,
            direction="outbound",
            provider_message_id=result["message_id"],
            wa_id=recipient,
            phone_to=recipient,
            phone_from=None,
            message_type=data.message_type,
            content=content,
            template_variables={},
            status=db_status,
            sent_at=datetime.now(UTC),
        )

        await self.repository.add_communication_log(
            tenant_id=tenant_id,
            customer_id=data.customer_id,
            lead_id=data.lead_id,
            whatsapp_message_id=row["id"],
            direction="outbound",
            status=db_status,
            summary=f"WhatsApp {data.message_type} message sent",
            initiated_by="user" if created_by else "system",
            initiated_by_id=created_by,
        )

        # Additive instrumentation only -- same session, no control-flow change.
        # conversation_id is left NULL here: the outbound send path is not wired
        # to the conversation layer in this phase (only inbound is), and a
        # guessed conversation would be worse than none.
        await publish_event(
            self.session,
            tenant_id=tenant_id,
            event_type=EventType.MESSAGE_SENT,
            contact_id=data.customer_id,
            lead_id=data.lead_id,
            payload={
                "channel": "whatsapp",
                "provider_message_id": result["message_id"],
                "message_type": data.message_type,
                "whatsapp_message_id": str(row["id"]),
                "initiated_by": "user" if created_by else "system",
            },
        )

        return WhatsAppMessageResponse.model_validate(row)

    async def list_messages(
        self,
        tenant_id: UUID,
        filters: WhatsAppMessageFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[WhatsAppMessageResponse]:
        """List/filter WhatsApp messages."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [WhatsAppMessageResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0
        return PaginatedResponse[WhatsAppMessageResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )

    async def list_templates(
        self, tenant_id: UUID, refresh: bool = False
    ) -> list[WhatsAppTemplateResponse]:
        """List WhatsApp templates -- a live passthrough to Meta, not a
        read of the local whatsapp_templates table."""
        client = await get_client_for_tenant(self.session, tenant_id)
        raw_templates = await client.list_templates(refresh=refresh)
        return [
            WhatsAppTemplateResponse(
                id=str(t.get("id", "")),
                name=t.get("name", ""),
                language=t.get("language", ""),
                status=t.get("status", ""),
                category=t.get("category", ""),
                parameter_format=t.get("parameter_format"),
                components=t.get("components", []),
            )
            for t in raw_templates
        ]

    async def _enforce_session_window(self, tenant_id: UUID, customer_id: UUID) -> None:
        """Reject a free-form (non-template) send outside WhatsApp's 24-hour
        customer-session window, with a clear, specific error -- rather
        than letting Meta's own rejection surface as a confusing generic
        client error after an HTTP round-trip we didn't need to make."""
        last_inbound = await self.repository.get_most_recent_inbound_message(
            tenant_id, customer_id
        )
        if not last_inbound:
            raise BusinessRuleError(
                message=(
                    "This customer has never messaged you on WhatsApp, so a free-form "
                    "message cannot be sent -- use a template message instead."
                ),
                code="WHATSAPP_SESSION_WINDOW_NEVER_OPENED",
            )

        window_start = datetime.now(UTC) - timedelta(hours=_SESSION_WINDOW_HOURS)
        last_inbound_at = last_inbound["created_at"]
        if last_inbound_at.tzinfo is None:
            last_inbound_at = last_inbound_at.replace(tzinfo=UTC)
        if last_inbound_at < window_start:
            raise BusinessRuleError(
                message=(
                    "Customer is outside the 24-hour WhatsApp messaging window "
                    "(their last message was more than 24 hours ago); use a template "
                    "message instead."
                ),
                code="WHATSAPP_SESSION_WINDOW_CLOSED",
            )


def _to_whatsapp_recipient(phone: str) -> str:
    """Superfone's `recipient` field is country-code-prefixed with NO leading
    `+` (e.g. "917545991999"), while this codebase's customers.phone is
    stored as normalized E.164 WITH a leading `+` (see
    app/customers/schemas.py::normalize_phone). Strip it here at the
    integration boundary rather than changing the stored format."""
    return phone.lstrip("+")
