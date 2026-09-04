"""WhatsApp Messaging Repository for PostgreSQL database operations."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.schemas import PaginationParams
from app.whatsapp.schemas import WhatsAppMessageFilter

logger = logging.getLogger(__name__)


class WhatsAppRepository:
    """Repository handling database access for whatsapp_messages /
    communication_logs. Template listing is a live API passthrough (see
    WhatsAppService), never read from whatsapp_templates here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_customer(self, tenant_id: UUID, customer_id: UUID) -> dict[str, Any] | None:
        """Fetch a customer, scoped to tenant."""
        result = await self.session.execute(
            text(
                "SELECT * FROM public.customers "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": customer_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_lead(self, tenant_id: UUID, lead_id: UUID) -> dict[str, Any] | None:
        """Fetch a lead, scoped to tenant."""
        result = await self.session.execute(
            text(
                "SELECT * FROM public.leads "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": lead_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_most_recent_inbound_message(
        self, tenant_id: UUID, customer_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch the customer's most recent inbound message, for the
        24-hour session-window check."""
        result = await self.session.execute(
            text(
                """
                SELECT created_at FROM public.whatsapp_messages
                WHERE tenant_id = :tenant_id AND customer_id = :customer_id
                  AND direction = 'inbound'::public.message_direction
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create_message(
        self,
        *,
        tenant_id: UUID,
        customer_id: UUID,
        lead_id: UUID | None,
        direction: str,
        provider_message_id: str | None,
        wa_id: str | None,
        phone_to: str | None,
        phone_from: str | None,
        message_type: str,
        content: dict[str, Any],
        template_variables: dict[str, Any],
        status: str,
        sent_at: datetime | None,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a new whatsapp_messages row (outbound send or inbound receive).

        `conversation_id` is optional and nullable (migration 030): rows written
        before conversations existed keep NULL, and any caller that has not been
        wired to the conversation layer yet still works unchanged.
        """
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.whatsapp_messages (
                    tenant_id, customer_id, lead_id, conversation_id, direction,
                    provider_message_id, wa_id, phone_to, phone_from, message_type,
                    content, template_variables, status, sent_at
                ) VALUES (
                    :tenant_id, :customer_id, :lead_id, :conversation_id,
                    CAST(:direction AS public.message_direction),
                    :provider_message_id, :wa_id, :phone_to, :phone_from, :message_type,
                    :content, :template_variables, CAST(:status AS public.whatsapp_message_status),
                    :sent_at
                )
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "direction": direction,
                "provider_message_id": provider_message_id,
                "wa_id": wa_id,
                "phone_to": phone_to,
                "phone_from": phone_from,
                "message_type": message_type,
                "content": content,
                "template_variables": template_variables,
                "status": status,
                "sent_at": sent_at,
            },
        )
        return dict(result.mappings().one())

    async def update_message_status_by_provider_id(
        self,
        provider_message_id: str,
        status: str,
        *,
        delivered_at: datetime | None = None,
        read_at: datetime | None = None,
        failed_at: datetime | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a message's delivery status by wamid. No tenant_id filter --
        wamids are globally unique (assigned by Meta), same as SFVoPI's
        call_uuid; if no row matches (e.g. a status event for a message we
        never sent through this integration), returns None and the caller
        skips the update rather than fabricating one."""
        result = await self.session.execute(
            text(
                """
                UPDATE public.whatsapp_messages
                SET status = CAST(:status AS public.whatsapp_message_status),
                    delivered_at = COALESCE(:delivered_at, delivered_at),
                    read_at = COALESCE(:read_at, read_at),
                    failed_at = COALESCE(:failed_at, failed_at),
                    failure_code = COALESCE(:failure_code, failure_code),
                    failure_reason = COALESCE(:failure_reason, failure_reason),
                    updated_at = NOW()
                WHERE provider_message_id = :provider_message_id
                RETURNING *
                """
            ),
            {
                "provider_message_id": provider_message_id,
                "status": status,
                "delivered_at": delivered_at,
                "read_at": read_at,
                "failed_at": failed_at,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    # NOTE: find_customer_by_phone / create_minimal_customer used to live here.
    # They were one of three copies of the same find-or-create and have been
    # replaced by the single tenant-scoped, race-safe path in
    # app/customers/resolver.py::ContactResolver.resolve_contact.
    # The old INSERT here had no ON CONFLICT clause, so two simultaneous
    # first-contact messages from the same number raised a raw IntegrityError;
    # the resolver does not.

    async def add_communication_log(
        self,
        *,
        tenant_id: UUID,
        customer_id: UUID,
        lead_id: UUID | None,
        whatsapp_message_id: UUID,
        direction: str,
        status: str,
        summary: str | None,
        initiated_by: str,
        initiated_by_id: UUID | None,
    ) -> None:
        """Append a row to the unified communication timeline."""
        await self.session.execute(
            text(
                """
                INSERT INTO public.communication_logs (
                    tenant_id, customer_id, lead_id, whatsapp_message_id,
                    channel, direction, status, summary, initiated_by, initiated_by_id
                ) VALUES (
                    :tenant_id, :customer_id, :lead_id, :whatsapp_message_id,
                    'whatsapp'::public.communication_channel,
                    CAST(:direction AS public.message_direction),
                    :status, :summary, :initiated_by, :initiated_by_id
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "lead_id": lead_id,
                "whatsapp_message_id": whatsapp_message_id,
                "direction": direction,
                "status": status,
                "summary": summary,
                "initiated_by": initiated_by,
                "initiated_by_id": initiated_by_id,
            },
        )

    # NOTE: get_lead_for_customer used to live here. Lead resolution is now the
    # single deterministic rule in app/leads/resolver.py::LeadResolver, which
    # additionally excludes closed leads and opens one when none is active.

    async def update_template_status_by_provider_id(
        self, provider_template_id: str, status: str, rejection_reason: str | None
    ) -> dict[str, Any] | None:
        """Update a template's approval status by Meta's template ID."""
        result = await self.session.execute(
            text(
                """
                UPDATE public.whatsapp_templates
                SET status = CAST(:status AS public.whatsapp_template_status),
                    rejection_reason = :rejection_reason,
                    updated_at = NOW()
                WHERE provider_template_id = :provider_template_id
                RETURNING *
                """
            ),
            {
                "provider_template_id": provider_template_id,
                "status": status,
                "rejection_reason": rejection_reason,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID,
        filters: WhatsAppMessageFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """List/filter WhatsApp messages for a tenant with customer and lead details."""
        where_conditions = ["wm.tenant_id = :tenant_id"]
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if filters.customer_id:
            where_conditions.append("wm.customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id
        if filters.lead_id:
            where_conditions.append("wm.lead_id = :filter_lead_id")
            params["filter_lead_id"] = filters.lead_id
        if filters.direction:
            where_conditions.append(
                "wm.direction = CAST(:filter_direction AS public.message_direction)"
            )
            params["filter_direction"] = filters.direction
        if filters.status:
            where_conditions.append(
                "wm.status = CAST(:filter_status AS public.whatsapp_message_status)"
            )
            params["filter_status"] = filters.status

        where_str = " AND ".join(where_conditions)
        count_result = await self.session.execute(
            text(f"SELECT COUNT(*) FROM public.whatsapp_messages wm WHERE {where_str}"),  # noqa: S608
            params,
        )
        total = count_result.scalar_one()

        select_result = await self.session.execute(
            text(
                f"""
                SELECT
                    wm.*,
                    c.full_name     AS customer_name,
                    c.phone         AS customer_phone,
                    c.email         AS customer_email,
                    c.city          AS customer_city,
                    l.lead_number   AS lead_number,
                    l.status::text  AS lead_status
                FROM public.whatsapp_messages wm
                LEFT JOIN public.customers c ON c.id = wm.customer_id
                LEFT JOIN public.leads l ON l.id = wm.lead_id
                WHERE {where_str}
                ORDER BY wm.created_at DESC
                LIMIT :limit OFFSET :offset
                """  # noqa: S608
            ),
            params,
        )
        return [dict(r) for r in select_result.mappings().all()], total
