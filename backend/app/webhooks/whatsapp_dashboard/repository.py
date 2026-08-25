"""Repository for the whatsapp-dashboard call-agent trigger: cross-tenant
phone lookup only -- the one legitimate remaining use of an untenanted
customers query, since this caller supplies no tenant at all.

Lead resolution moved to app/leads/resolver.py::LeadResolver (call_jobs.lead_id
is NOT NULL, so a lead is still mandatory before a call_job can be created --
the service resolves one there)."""

import logging
from typing import Any

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

    # NOTE: get_or_create_lead used to live here. It was one of three copies of
    # the same "most recent lead, else create" rule; lead resolution is now the
    # single deterministic path in app/leads/resolver.py::LeadResolver, which
    # additionally excludes closed leads (converted/lost/do_not_contact) rather
    # than reviving one to hang a new call off.
