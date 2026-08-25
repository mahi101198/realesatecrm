"""Conversation persistence -- just enough for callers to get a conversation_id.

Race safety mirrors the contact resolver: the INSERT names the partial unique
index `uq_conversations_open_contact_channel` as its ON CONFLICT arbiter (the
`WHERE status = 'open'` predicate MUST be repeated or Postgres cannot infer a
partial index and raises 42P10 -- the same trap that migration 016 documents
for `webhook_events`). Losing the race re-fetches the winner's row.

Every query is tenant-scoped: `tenant_id` is in the WHERE clause of the lookup
and in the INSERT column list.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.model import ConversationChannel
from app.core.exceptions import ConflictError

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Database access for `public.conversations`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_open_conversation(
        self, tenant_id: UUID, contact_id: UUID, channel: str
    ) -> dict[str, Any] | None:
        """Fetch this contact's open thread on `channel`, scoped to tenant."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.conversations
                WHERE tenant_id = :tenant_id
                  AND contact_id = :contact_id
                  AND channel = CAST(:channel AS public.conversation_channel)
                  AND status = 'open'::public.conversation_status
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "contact_id": contact_id, "channel": channel},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create_conversation(
        self,
        tenant_id: UUID,
        contact_id: UUID,
        channel: str,
        lead_id: UUID | None = None,
        external_thread_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Race-safe INSERT. Returns None if another request opened it first."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.conversations (
                    tenant_id, contact_id, lead_id, channel, external_thread_id, status
                ) VALUES (
                    :tenant_id, :contact_id, :lead_id,
                    CAST(:channel AS public.conversation_channel), :external_thread_id,
                    'open'::public.conversation_status
                )
                ON CONFLICT (tenant_id, contact_id, channel)
                    WHERE status = 'open'::public.conversation_status
                    DO NOTHING
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "contact_id": contact_id,
                "lead_id": lead_id,
                "channel": channel,
                "external_thread_id": external_thread_id,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def backfill_conversation_details(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        lead_id: UUID | None = None,
        external_thread_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fill in `lead_id` / `external_thread_id` on an existing OPEN thread
        that was opened before they were known. Only ever writes NULL columns
        (COALESCE keeps whatever is already there), so this can never reassign
        a conversation to a different lead."""
        if lead_id is None and external_thread_id is None:
            return None
        result = await self.session.execute(
            text(
                """
                UPDATE public.conversations
                SET lead_id = COALESCE(lead_id, :lead_id),
                    external_thread_id = COALESCE(external_thread_id, :external_thread_id),
                    updated_at = NOW()
                WHERE id = :id AND tenant_id = :tenant_id
                RETURNING *
                """
            ),
            {
                "id": conversation_id,
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "external_thread_id": external_thread_id,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None


async def get_or_create_conversation(
    session: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    channel: str | ConversationChannel,
    external_thread_id: str | None = None,
    lead_id: UUID | None = None,
) -> dict[str, Any]:
    """Return the contact's OPEN conversation on `channel`, opening one if needed.

    Deterministic and idempotent: called twice for the same
    (tenant_id, contact_id, channel) it returns the same row.

    `lead_id` and `external_thread_id` are backfilled onto an existing open
    thread only where those columns are still NULL -- resolution never
    reassigns an established conversation.
    """
    channel_value = str(channel)
    repo = ConversationRepository(session)

    existing = await repo.get_open_conversation(tenant_id, contact_id, channel_value)
    if existing:
        needs_lead = lead_id is not None and existing.get("lead_id") is None
        needs_thread = (
            external_thread_id is not None and existing.get("external_thread_id") is None
        )
        if needs_lead or needs_thread:
            updated = await repo.backfill_conversation_details(
                tenant_id,
                existing["id"],
                lead_id=lead_id if needs_lead else None,
                external_thread_id=external_thread_id if needs_thread else None,
            )
            if updated:
                return updated
        return existing

    created = await repo.create_conversation(
        tenant_id,
        contact_id,
        channel_value,
        lead_id=lead_id,
        external_thread_id=external_thread_id,
    )
    if created:
        return created

    winner = await repo.get_open_conversation(tenant_id, contact_id, channel_value)
    if winner:
        return winner

    # Only reachable if the winning row was closed in the instant between our
    # ON CONFLICT DO NOTHING and this re-fetch.
    raise ConflictError(
        message="Could not open a conversation due to a temporary conflict. Please try again.",
        code="CONVERSATION_CREATE_CONFLICT",
    )
