"""Public Lead Intake Business Service Layer.

Tenant resolution: NEVER trusts a client-supplied tenant_id. The tenant is
resolved server-side from a public, opaque, already-existing identifier --
tenants.slug (unique, set at tenant onboarding, already used for other
purposes in this schema) -- passed in the URL PATH, not the request body.
If the slug does not resolve to an active tenant, this fails CLOSED: a
generic 404 is returned, the same response an unknown slug and an inactive
tenant both produce, so the response itself cannot be used to enumerate
which tenant slugs exist.

Find-or-create: deliberately NOT CustomerService.create_customer (which 409s
on duplicate phone -- the wrong contract for anonymous repeat visitors). The
find-or-create itself now lives in the shared deterministic foundation layer,
app/customers/resolver.py::ContactResolver -- this module no longer carries its
own copy of that logic.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.customers.resolver import ContactResolver
from app.db.transaction import atomic
from app.public_intake.repository import PublicIntakeRepository
from app.public_intake.schemas import PublicLeadIntakeRequest, PublicLeadIntakeResponse

logger = logging.getLogger(__name__)


class PublicIntakeService:
    """Service handling the anonymous website/campaign lead intake flow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PublicIntakeRepository(session)
        self.contact_resolver = ContactResolver(session)

    async def resolve_tenant_id(self, tenant_slug: str) -> UUID:
        """Resolve tenant_id from the public slug. Fails closed (404) if the
        slug does not resolve to an active tenant."""
        tenant = await self.repository.resolve_tenant_by_slug(tenant_slug)
        if not tenant:
            raise NotFoundError(
                message="This intake link is not available.",
                code="INTAKE_LINK_NOT_FOUND",
            )
        return UUID(str(tenant["id"]))

    async def submit_enquiry(
        self, tenant_id: UUID, data: PublicLeadIntakeRequest
    ) -> PublicLeadIntakeResponse:
        """Find-or-create the customer by (tenant_id, phone), create a lead for
        them, and optionally record property interest -- all scoped to the
        server-resolved tenant_id only."""
        async with atomic(self.session):
            lead_source_id = await self.repository.resolve_lead_source_id(
                tenant_id, data.source_code
            )

            customer = await self._find_or_create_customer(tenant_id, data, lead_source_id)

            lead = await self.repository.create_minimal_lead(
                tenant_id=tenant_id,
                customer_id=customer["id"],
                lead_source_id=lead_source_id,
                notes=data.message,
            )

            await self._maybe_record_property_interest(tenant_id, lead["id"], data.property_id)

        return PublicLeadIntakeResponse(reference=lead["lead_number"])

    async def _find_or_create_customer(
        self,
        tenant_id: UUID,
        data: PublicLeadIntakeRequest,
        lead_source_id: UUID | None,
    ) -> dict[str, Any]:
        """Thin adapter over the shared ContactResolver.

        Behaviour is unchanged from the copy that used to live here: genuine
        find-or-create on (tenant_id, phone) -- a returning phone number is not
        an error, it is the expected case for repeat visitors -- and the race
        between two simultaneous first-time submissions for the same new number
        is resolved by re-fetching the winner's row rather than 500ing. Both of
        those now happen inside ContactResolver.resolve_contact, including the
        same CUSTOMER_CREATE_CONFLICT error code on the pathological path.
        """
        # `email` is passed as a CREATE-time default only, never as a lookup
        # key: identity here is (tenant_id, phone), exactly as before. Matching
        # an anonymous web submission onto an existing contact by email alone
        # would be a new, weaker identity rule -- out of scope for a
        # consolidation refactor.
        return await self.contact_resolver.resolve_contact(
            tenant_id,
            phone=data.phone,
            defaults={
                "full_name": data.name,
                "email": data.email,
                "lead_source_id": lead_source_id,
                "source": "public_intake",
            },
        )

    async def _maybe_record_property_interest(
        self, tenant_id: UUID, lead_id: UUID, raw_property_id: str | None
    ) -> None:
        """Best-effort: if property_id is present and valid, record interest.
        Silently skips on a malformed/unknown value rather than failing the
        whole enquiry -- see PublicLeadIntakeRequest.property_id docstring."""
        if not raw_property_id:
            return
        try:
            property_id = UUID(raw_property_id)
        except ValueError:
            logger.info(f"Public intake: ignoring malformed property_id '{raw_property_id}'.")
            return

        prop = await self.repository.get_property_for_interest(tenant_id, property_id)
        if not prop:
            logger.info(
                f"Public intake: ignoring unknown/cross-tenant property_id '{raw_property_id}'."
            )
            return

        await self.repository.add_property_interest(
            tenant_id, lead_id, prop["project_id"], prop["id"]
        )
