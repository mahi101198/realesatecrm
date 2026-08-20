"""Repository for the whatsapp-dashboard call-agent trigger: cross-tenant
phone lookup (the one legitimate remaining use, since the caller supplies
no tenant) and lead resolution/auto-create (call_jobs.lead_id is NOT NULL,
so a lead is mandatory before a call_job can be created)."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class CallAgentTriggerRepository:
    """Repository backing the whatsapp-dashboard call-agent trigger flow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_customer_by_phone_cross_tenant(self, phone: str) -> dict[str, Any] | None:
        """Look up a customer by phone WITHOUT a tenant_id filter. Returns
        the single matching row ONLY if the phone number is unambiguous
        (exactly one non-deleted customer across ALL tenants has it) -- if
        zero or two-or-more tenants have a customer with this phone,
        returns None so the caller treats it as unattributable rather than
        guessing which tenant it belongs to."""
        result = await self.session.execute(
            text("SELECT * FROM public.customers WHERE phone = :phone AND deleted_at IS NULL"),
            {"phone": phone},
        )
        rows = result.mappings().all()
        if len(rows) != 1:
            return None
        return dict(rows[0])

    async def get_or_create_lead(self, tenant_id: UUID, customer_id: UUID) -> dict[str, Any]:
        """Fetch the customer's most recent lead, or auto-create a minimal
        one. call_jobs.lead_id is NOT NULL, so this is mandatory before
        queuing/placing a call."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.leads
                WHERE tenant_id = :tenant_id AND customer_id = :customer_id
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        row = result.mappings().one_or_none()
        if row:
            return dict(row)

        insert_result = await self.session.execute(
            text(
                """
                INSERT INTO public.leads (tenant_id, customer_id)
                VALUES (:tenant_id, :customer_id)
                RETURNING *
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        return dict(insert_result.mappings().one())
