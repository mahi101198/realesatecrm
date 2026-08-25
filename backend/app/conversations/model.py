"""SQLAlchemy ORM model for `public.conversations` (migration 030)."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationChannel(StrEnum):
    """Values of the `public.conversation_channel` Postgres enum."""

    WHATSAPP = "whatsapp"
    VOICE = "voice"


class ConversationStatus(StrEnum):
    """Values of the `public.conversation_status` Postgres enum."""

    OPEN = "open"
    CLOSED = "closed"


class Conversation(Base):
    """One thread per (tenant, contact, channel).

    At most one OPEN row per combination -- enforced by the partial unique
    index `uq_conversations_open_contact_channel`, which is also the ON CONFLICT
    arbiter used by `get_or_create_conversation`.
    """

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )

    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default=ConversationStatus.OPEN)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
