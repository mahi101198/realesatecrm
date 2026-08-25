"""Contact Resolver -- the ONE find-or-create path for a contact.

WHY THIS LIVES IN app/customers/ AND NOT app/contacts/
------------------------------------------------------
In this schema the "Contact" entity IS `public.customers`: one row per real
person, with `uq_customers_tenant_phone_active` as its identity key. Adding an
`app/contacts/` package would give one table two names in the codebase and
invite a second, divergent set of helpers -- exactly the duplication this
module exists to remove. So the resolver sits beside the model/repository/
service that already own `customers`, and "contact" is used only as the
vocabulary of the foundation layer.

SCOPE -- deliberately tiny
--------------------------
`resolve_contact` normalizes, looks up, and creates. That is all. No
conversation state, no lead logic, no scoring, no marketing decisions, no AI.
Callers that need a lead call `app.leads.resolver.LeadResolver`.

TENANT SCOPING
--------------
`tenant_id` is required and appears in the WHERE clause of every lookup and in
every INSERT. There is no cross-tenant path here by construction. (The one
legitimate cross-tenant phone lookup in this codebase --
`CallAgentTriggerRepository.find_customer_by_phone_cross_tenant`, where the
caller supplies no tenant at all -- stays where it is; it is a different
operation with a different, documented safety rule.)

RACE SAFETY
-----------
Creation uses the same `ON CONFLICT (tenant_id, phone) WHERE deleted_at IS
NULL DO NOTHING` arbiter that `PublicIntakeRepository.create_minimal_customer`
used, matching the partial unique index `uq_customers_tenant_phone_active`
(migration 014). Losing the race is not a failure: the winner's row is just as
valid, so it is re-fetched and returned.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationError
from app.customers.schemas import normalize_phone
from app.events.model import EventType
from app.events.publisher import publish_event

logger = logging.getLogger(__name__)


class ContactResolver:
    """Deterministic find-or-create for `public.customers`, scoped to a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_contact(
        self,
        tenant_id: UUID,
        phone: str | None = None,
        email: str | None = None,
        external_id: str | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the contact for this tenant, creating one if none exists.

        Lookup order (first hit wins): phone -> email -> external_id. `phone`
        is normalized through `app.customers.schemas.normalize_phone`, the same
        function every customer-facing Pydantic schema already uses, so a
        contact reached via WhatsApp, the public intake form, or a staff
        endpoint always resolves to the same row.

        `defaults` seeds columns on CREATION ONLY -- an existing contact is
        never mutated by resolution. Recognised keys: full_name, first_name,
        last_name, email, lead_source_id, preferred_language, whatsapp_opted_in,
        metadata. Unknown keys are ignored.

        `external_id` is stored in / read from `customers.metadata->>'external_id'`
        (the table has no dedicated column and this phase adds no breaking
        schema change). It is a fallback identifier only; phone remains the
        real identity key enforced by the database.

        Returns the full customer row as a dict, exactly like the repositories
        it replaces.
        """
        defaults = defaults or {}

        normalized_phone: str | None = None
        if phone:
            # Raise a typed error rather than letting a raw ValueError escape
            # into a webhook handler.
            try:
                normalized_phone = normalize_phone(phone)
            except ValueError as e:
                raise ValidationError(
                    message="The supplied phone number is not a valid format.",
                    code="INVALID_PHONE_NUMBER",
                ) from e

        if not normalized_phone and not email and not external_id:
            raise ValidationError(
                message="A phone number, email, or external id is required to resolve a contact.",
                code="CONTACT_IDENTIFIER_REQUIRED",
            )

        existing = await self._find(tenant_id, normalized_phone, email, external_id)
        if existing:
            return existing

        if not normalized_phone:
            # customers.phone is NOT NULL. An email/external-id-only caller can
            # look a contact up but cannot conjure one.
            raise ValidationError(
                message="A phone number is required to create a new contact.",
                code="CONTACT_PHONE_REQUIRED",
            )

        created = await self._create(tenant_id, normalized_phone, email, external_id, defaults)
        if created:
            await publish_event(
                self.session,
                tenant_id=tenant_id,
                event_type=EventType.CONTACT_CREATED,
                contact_id=created["id"],
                payload={
                    "source": defaults.get("source", "contact_resolver"),
                    "phone": normalized_phone,
                },
            )
            return created

        # Lost the INSERT race -- the other request's row is just as valid.
        winner = await self._find(tenant_id, normalized_phone, None, None)
        if winner:
            return winner

        # Vanishingly unlikely: the winning row was soft-deleted between our
        # ON CONFLICT DO NOTHING and this re-fetch. Surface a clean typed error
        # rather than returning no contact.
        raise ConflictError(
            message=(
                "Could not resolve this contact due to a temporary conflict. "
                "Please try again."
            ),
            code="CUSTOMER_CREATE_CONFLICT",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find(
        self,
        tenant_id: UUID,
        phone: str | None,
        email: str | None,
        external_id: str | None,
    ) -> dict[str, Any] | None:
        """Tenant-scoped lookup by phone, then email, then external_id."""
        if phone:
            row = await self._select_one(
                """
                SELECT * FROM public.customers
                WHERE tenant_id = :tenant_id AND phone = :phone AND deleted_at IS NULL
                """,
                {"tenant_id": tenant_id, "phone": phone},
            )
            if row:
                return row

        if email:
            # Not uniquely constrained -- take the oldest match so repeated
            # resolution is stable rather than flapping between duplicates.
            row = await self._select_one(
                """
                SELECT * FROM public.customers
                WHERE tenant_id = :tenant_id AND lower(email) = lower(:email)
                  AND deleted_at IS NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                {"tenant_id": tenant_id, "email": email},
            )
            if row:
                return row

        if external_id:
            row = await self._select_one(
                """
                SELECT * FROM public.customers
                WHERE tenant_id = :tenant_id
                  AND metadata->>'external_id' = :external_id
                  AND deleted_at IS NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                {"tenant_id": tenant_id, "external_id": external_id},
            )
            if row:
                return row

        return None

    async def _create(
        self,
        tenant_id: UUID,
        phone: str,
        email: str | None,
        external_id: str | None,
        defaults: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Race-safe minimal INSERT. Returns None if another request won."""
        metadata: dict[str, Any] = dict(defaults.get("metadata") or {})
        if external_id:
            metadata["external_id"] = external_id

        result = await self.session.execute(
            text(
                """
                INSERT INTO public.customers (
                    tenant_id, full_name, phone, email, lead_source_id,
                    preferred_language, whatsapp_opted_in, metadata
                ) VALUES (
                    :tenant_id, :full_name, :phone, :email, :lead_source_id,
                    COALESCE(:preferred_language, 'hi'),
                    COALESCE(:whatsapp_opted_in, false), :metadata
                )
                ON CONFLICT (tenant_id, phone) WHERE deleted_at IS NULL DO NOTHING
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                # customers.full_name is NOT NULL; the phone is the honest
                # fallback label for a first-contact stranger.
                "full_name": defaults.get("full_name") or phone,
                "phone": phone,
                "email": email or defaults.get("email"),
                "lead_source_id": defaults.get("lead_source_id"),
                "preferred_language": defaults.get("preferred_language"),
                "whatsapp_opted_in": defaults.get("whatsapp_opted_in"),
                # asyncpg's jsonb codec expects an already-serialized string, not
                # a dict -- a raw dict here raises DataError on every real insert
                # (mocked tests never catch this, since they never serialize).
                "metadata": json.dumps(metadata, default=str),
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def _select_one(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        result = await self.session.execute(text(sql), params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None
