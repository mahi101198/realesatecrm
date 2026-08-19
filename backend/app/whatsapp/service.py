"""WhatsApp Messaging Business Service Layer."""

import logging
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
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

# Superfone's success response reports message_status="accepted"; our DB
# enum (queued/sent/delivered/read/failed) has no "accepted" value -- the
# closest honest fit is "sent" (we handed it off successfully). Anything
# else unrecognized also falls back to "sent" rather than failing the send
# that has, per the client's own 200-but-confirmed check, already succeeded.
_PROVIDER_STATUS_TO_DB_STATUS = {
    "accepted": "sent",
    "sent": "sent",
}


class WhatsAppService:
    """Service for sending/listing WhatsApp messages and listing templates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WhatsAppRepository(session)

    async def send_message(
        self, tenant_id: UUID, created_by: UUID | None, data: WhatsAppSendRequest  # noqa: ARG002
    ) -> WhatsAppMessageResponse:
        """Send a WhatsApp message, enforcing the 24-hour session-window
        rule for non-template message types before ever calling Superfone."""
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

        raise NotImplementedError(
            "WhatsApp messaging via Superfone has been removed. "
            "Awaiting Meta WhatsApp Cloud API integration."
        )

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

    async def list_templates(self, refresh: bool = False) -> list[WhatsAppTemplateResponse]:
        """List WhatsApp templates -- a live passthrough to Superfone/Meta,
        not a read of the local whatsapp_templates table."""
        raise NotImplementedError(
            "WhatsApp template listing via Superfone has been removed. "
            "Awaiting Meta WhatsApp Cloud API integration."
        )

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
