"""Domain event publisher.

DB-only. `publish_event` appends one row to `public.events` using the SAME
session as the state change it records, so the event is committed atomically
with that change and rolled back with it -- an event can never claim something
happened that did not.

Deliberately NOT swallowing errors: a failed INSERT inside an open transaction
poisons that transaction anyway, so catching-and-continuing would only turn a
clear failure into a confusing one further downstream. Callers keep their
existing control flow because this function does exactly one thing (an INSERT)
and returns nothing they need to branch on.

Tenant scoping: `tenant_id` is REQUIRED and always written. There is no
"global" event -- every row in public.events belongs to exactly one tenant.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.model import EventType

logger = logging.getLogger(__name__)

_INSERT_EVENT_SQL = text(
    """
    INSERT INTO public.events (
        tenant_id, event_type, contact_id, lead_id, conversation_id, payload
    ) VALUES (
        :tenant_id, :event_type, :contact_id, :lead_id, :conversation_id, :payload
    )
    RETURNING id
    """
)


async def publish_event(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: EventType | str,
    contact_id: UUID | None = None,
    lead_id: UUID | None = None,
    conversation_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> UUID | None:
    """Append a domain event for `tenant_id`. Returns the new event id.

    `event_type` should be an `EventType` member; a bare string is accepted so
    an emitter can be added without editing the enum first, but every emitter
    in this repository uses the enum.

    Returns None only when `tenant_id` is falsy -- a defensive no-op rather
    than writing an untenanted row, since public.events.tenant_id is NOT NULL
    and a row with a guessed tenant would break tenant isolation.
    """
    if not tenant_id:
        logger.warning(
            f"publish_event called without a tenant_id for event_type="
            f"{str(event_type)!r}; skipping (events are always tenant-scoped)."
        )
        return None

    result = await session.execute(
        _INSERT_EVENT_SQL,
        {
            "tenant_id": tenant_id,
            "event_type": str(event_type),
            "contact_id": contact_id,
            "lead_id": lead_id,
            "conversation_id": conversation_id,
            # asyncpg's jsonb codec expects an already-serialized string, not a
            # dict -- a raw dict raises DataError on every real insert (mocked
            # tests never catch this, since they never serialize parameters).
            "payload": json.dumps(payload or {}, default=str),
        },
    )
    row = result.mappings().one_or_none()
    return row["id"] if row else None
