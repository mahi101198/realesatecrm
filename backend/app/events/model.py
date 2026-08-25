"""Domain event types and the `public.events` ORM model.

`EventType` is the authoritative list of values this backend writes into
`public.events.event_type`. The column is TEXT (see migration 031), so adding a
member here never requires a migration -- but every emitter in this codebase
MUST use a member of this enum rather than a bare string, so the set of
observable transitions stays discoverable from one place.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EventType(StrEnum):
    """Business transitions recorded in `public.events`.

    Members marked "not emitted yet" have no deterministic emission point in
    this phase -- they exist so phase 2 does not have to invent names, and so
    the vocabulary is fixed before an orchestrator starts depending on it.
    """

    # --- Contact / lead lifecycle -----------------------------------------
    CONTACT_CREATED = "contact_created"
    LEAD_CREATED = "lead_created"
    LEAD_UPDATED = "lead_updated"  # not emitted yet (phase 2)
    QUALIFICATION_COMPLETED = "qualification_completed"  # not emitted yet (phase 2)

    # --- Messaging ---------------------------------------------------------
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"

    # --- Voice calls -------------------------------------------------------
    CALL_REQUESTED = "call_requested"
    CALL_SCHEDULED = "call_scheduled"
    CALL_STARTED = "call_started"
    CALL_COMPLETED = "call_completed"
    CALL_FAILED = "call_failed"

    # --- Escalation --------------------------------------------------------
    HUMAN_HANDOFF_REQUESTED = "human_handoff_requested"

    # --- Site visits / bookings -------------------------------------------
    VISIT_REQUESTED = "visit_requested"  # not emitted yet (phase 2)
    VISIT_SCHEDULED = "visit_scheduled"  # not emitted yet (phase 2)
    VISIT_COMPLETED = "visit_completed"  # not emitted yet (phase 2)
    BOOKING_CREATED = "booking_created"  # not emitted yet (phase 2)


class Event(Base):
    """Append-only domain event row. Never updated, never deleted."""

    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )

    event_type: Mapped[str] = mapped_column(Text, nullable=False)

    contact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
