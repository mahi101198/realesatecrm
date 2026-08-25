"""Lead Resolver -- the ONE find-or-create path for a contact's active lead.

THE RULE (deterministic, no AI, no scoring)
-------------------------------------------
    "Reuse the most recently created OPEN lead for this contact within this
     tenant; if there is none, create one."

OPEN means `leads.status NOT IN ('converted', 'lost', 'do_not_contact')` --
i.e. everything that is still workable ('new', 'active', 'on_hold'). Soft-
deleted leads are excluded. See migration 002 for the `lead_status` enum.

WHY THIS IS A HOOK, NOT A MATCHER
---------------------------------
The rule above is knowingly naive in one case: a contact with SEVERAL open
leads for DIFFERENT projects. "Most recent" is then a guess -- the caller may
actually be asking about the older lead. Disambiguating that needs the
conversation content, which is phase 2's job (an AI-assisted matcher).

This module is the seam where that matcher will plug in: everything already
funnels through `resolve_lead`, so phase 2 changes ONE function body instead of
hunting down ad-hoc lead lookups again. `context` is threaded through today
(and stored on creation) precisely so the future matcher has something to
match on without another call-site migration.

TENANT SCOPING
--------------
`tenant_id` is required and appears in the WHERE clause of every query and in
the INSERT. There is no cross-tenant path here.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.model import EventType
from app.events.publisher import publish_event

logger = logging.getLogger(__name__)

# Statuses that mean "this lead is finished, do not reuse it".
CLOSED_LEAD_STATUSES = ("converted", "lost", "do_not_contact")


class LeadResolver:
    """Deterministic find-or-create for `public.leads`, scoped to a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_lead(
        self,
        tenant_id: UUID,
        contact_id: UUID,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return this contact's active lead, creating one if none is open.

        `context` is an optional free-form dict describing where the resolution
        request came from, e.g.
        `{"source": "whatsapp", "project_interest": "...", "notes": "..."}`.
        It seeds fields on CREATION ONLY -- an existing open lead is never
        mutated by resolution (that is `LeadService.update_lead`'s job).

        Recognised keys on creation:
          * `source`            -- a `lead_sources.code`, resolved tenant-first
          * `lead_source_id`    -- an explicit id, wins over `source`
          * `notes`             -- free text stored on `leads.notes`
          * anything else       -- kept verbatim under
                                   `leads.metadata->'resolver_context'`

        Returns the full lead row as a dict.
        """
        context = context or {}

        existing = await self._find_open_lead(tenant_id, contact_id)
        if existing:
            return existing

        created = await self._create_lead(tenant_id, contact_id, context)

        await publish_event(
            self.session,
            tenant_id=tenant_id,
            event_type=EventType.LEAD_CREATED,
            contact_id=contact_id,
            lead_id=created["id"],
            payload={
                "source": context.get("source", "lead_resolver"),
                "lead_number": str(created.get("lead_number")),
            },
        )
        return created

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find_open_lead(
        self, tenant_id: UUID, contact_id: UUID
    ) -> dict[str, Any] | None:
        """Most recently created non-closed, non-deleted lead for this contact.

        PLACEHOLDER: when several open leads exist for different projects this
        picks the newest. Phase 2 replaces this body with an AI-assisted match
        that reads the conversation; the signature stays the same.
        """
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.leads
                WHERE tenant_id = :tenant_id
                  AND customer_id = :contact_id
                  AND deleted_at IS NULL
                  AND status NOT IN ('converted', 'lost', 'do_not_contact')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def _resolve_lead_source_id(
        self, tenant_id: UUID, source_code: str | None
    ) -> UUID | None:
        """Resolve a `lead_sources.id` from a code, preferring a tenant-specific
        source over the platform default of the same code. Same query
        PublicIntakeRepository uses -- an unknown code is not an error, the lead
        is simply created without a source."""
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

    async def _create_lead(
        self, tenant_id: UUID, contact_id: UUID, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Insert a minimal lead. `lead_number` is generated by the schema's
        own trigger (migration 005), never by this code."""
        lead_source_id = context.get("lead_source_id")
        if lead_source_id is None:
            lead_source_id = await self._resolve_lead_source_id(tenant_id, context.get("source"))

        # Everything the caller passed that is not a first-class column is kept
        # verbatim, so phase 2's matcher can inspect why a lead was opened.
        resolver_context = {
            k: str(v)
            for k, v in context.items()
            if k not in ("lead_source_id", "notes") and v is not None
        }
        metadata: dict[str, Any] = {}
        if resolver_context:
            metadata["resolver_context"] = resolver_context

        result = await self.session.execute(
            text(
                """
                INSERT INTO public.leads (
                    tenant_id, customer_id, lead_source_id, notes, status, sales_stage, metadata
                ) VALUES (
                    :tenant_id, :contact_id, :lead_source_id, :notes,
                    'new'::public.lead_status, 'new'::public.sales_stage, :metadata
                )
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "contact_id": contact_id,
                "lead_source_id": lead_source_id,
                "notes": context.get("notes"),
                # asyncpg's jsonb codec expects an already-serialized string, not
                # a dict -- see app.customers.resolver for the same fix.
                "metadata": json.dumps(metadata, default=str),
            },
        )
        return dict(result.mappings().one())
