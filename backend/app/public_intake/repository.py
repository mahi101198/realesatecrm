"""Public Lead Intake Repository for PostgreSQL database operations.

Deliberately self-contained: does not call into CustomerRepository /
LeadRepository (which are staff-facing and enforce a different, 409-on-
duplicate contract) -- see PublicIntakeService for the find-or-create logic
this repository supports.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PublicIntakeRepository:
    """Repository handling database access for the public lead intake flow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_tenant_by_slug(self, tenant_slug: str) -> dict[str, Any] | None:
        """Resolve a tenant from its public, server-controlled slug.

        Only ever returns an active, non-deleted tenant -- callers must treat
        None as "fail closed" (404), never fall back to any client-supplied
        tenant identifier.
        """
        result = await self.session.execute(
            text(
                """
                SELECT id, is_active FROM public.tenants
                WHERE slug = :slug AND is_active = true AND deleted_at IS NULL
                """
            ),
            {"slug": tenant_slug},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def resolve_lead_source_id(self, tenant_id: UUID, source_code: str | None) -> UUID | None:
        """Resolve a lead_sources.id from a source_code, preferring a
        tenant-specific source over the platform default of the same code."""
        if not source_code:
            return None
        result = await self.session.execute(
            text(
                """
                SELECT id FROM public.lead_sources
                WHERE code = :code AND is_active = true
                  AND (tenant_id = :tenant_id OR tenant_id IS NULL)
                ORDER BY tenant_id NULLS LAST
                LIMIT 1
                """
            ),
            {"code": source_code, "tenant_id": tenant_id},
        )
        return result.scalar_one_or_none()

    # NOTE: get_customer_by_phone / create_minimal_customer used to live here.
    # Their find-or-create (including the ON CONFLICT DO NOTHING arbiter that
    # matches uq_customers_tenant_phone_active) is now the single shared path
    # in app/customers/resolver.py::ContactResolver.resolve_contact.
    #
    # create_minimal_lead below is intentionally NOT replaced by LeadResolver:
    # a public enquiry always opens a NEW lead and returns its lead_number as
    # the visitor's reference, whereas LeadResolver deliberately REUSES an open
    # lead. Those are different contracts, not duplicated logic.

    async def create_minimal_lead(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        lead_source_id: UUID | None,
        notes: str | None,
    ) -> dict[str, Any]:
        """Insert a minimal lead record from public intake fields only."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.leads (
                    tenant_id, customer_id, lead_source_id, notes, status, sales_stage
                ) VALUES (
                    :tenant_id, :customer_id, :lead_source_id, :notes,
                    'new'::public.lead_status, 'new'::public.sales_stage
                )
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "lead_source_id": lead_source_id,
                "notes": notes,
            },
        )
        return dict(result.mappings().one())

    async def get_property_for_interest(
        self, tenant_id: UUID, property_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch a property's project_id, scoped to tenant, for recording interest."""
        result = await self.session.execute(
            text(
                """
                SELECT id, project_id FROM public.properties
                WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            ),
            {"id": property_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def add_property_interest(
        self, tenant_id: UUID, lead_id: UUID, project_id: UUID, property_id: UUID
    ) -> None:
        """Record the enquirer's property interest, mirroring the staff-facing
        lead_property_interests insert shape."""
        await self.session.execute(
            text(
                """
                INSERT INTO public.lead_property_interests (
                    tenant_id, lead_id, project_id, property_id, interest_level, is_primary
                ) VALUES (
                    :tenant_id, :lead_id, :project_id, :property_id,
                    'medium'::public.interest_level, true
                )
                ON CONFLICT (lead_id, project_id, property_id) DO NOTHING
                """
            ),
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "project_id": project_id,
                "property_id": property_id,
            },
        )
