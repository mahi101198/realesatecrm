"""Persistence for voice-call artefacts, using tables that ALREADY EXIST.

NO NEW TABLES. Migration 006 (`006_voice_ai.sql`) already defines exactly the
three shapes this package needs, and they were designed for this:

    public.call_messages          one row per utterance / tool call, append-only
    public.agent_sessions         the agent's durable per-call working state
    public.conversation_summaries the post-call structured intelligence

A "voice_call_results" table would have duplicated the third one. The call's
own lifecycle (status, retry policy, events) is NOT written here at all -- that
belongs to `AgentGateway.record_call_completed` and nowhere else, so this module
deliberately never touches `call_jobs`, `call_attempts` or `calls`.

Every statement is tenant-scoped. `call_id` is a foreign key into
`public.calls`, whose tenant we already resolved in `context.py`; the tenant is
still passed and written explicitly so a mis-resolved call id cannot leak a row
into another tenant's data.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Speakers, matching public.message_speaker.
SPEAKER_CUSTOMER = "customer"
SPEAKER_AGENT = "agent"
SPEAKER_SYSTEM = "system"

_INSERT_CALL_MESSAGE_SQL = text(
    """
    INSERT INTO public.call_messages (
        tenant_id, call_id, sequence_number, speaker, message_type,
        message_text, intent, language, tool_name, tool_args, tool_result, metadata
    ) VALUES (
        :tenant_id, :call_id, :sequence_number, CAST(:speaker AS public.message_speaker),
        CAST(:message_type AS public.message_type), :message_text, :intent, :language,
        :tool_name, :tool_args, :tool_result, :metadata
    )
    ON CONFLICT (call_id, sequence_number) DO NOTHING
    RETURNING id
    """
)

_UPSERT_AGENT_SESSION_SQL = text(
    """
    INSERT INTO public.agent_sessions (
        tenant_id, call_id, lead_id, customer_id, status, current_intent,
        detected_language, conversation_summary, extracted_requirements, ended_at
    ) VALUES (
        :tenant_id, :call_id, :lead_id, :customer_id,
        CAST(:status AS public.agent_session_status), :current_intent,
        :detected_language, :conversation_summary, :extracted_requirements, :ended_at
    )
    ON CONFLICT (call_id) DO UPDATE SET
        status = EXCLUDED.status,
        current_intent = COALESCE(EXCLUDED.current_intent, agent_sessions.current_intent),
        detected_language = COALESCE(
            EXCLUDED.detected_language, agent_sessions.detected_language
        ),
        conversation_summary = COALESCE(
            EXCLUDED.conversation_summary, agent_sessions.conversation_summary
        ),
        extracted_requirements = EXCLUDED.extracted_requirements,
        ended_at = COALESCE(EXCLUDED.ended_at, agent_sessions.ended_at),
        updated_at = NOW()
    RETURNING id
    """
)

_UPSERT_CONVERSATION_SUMMARY_SQL = text(
    """
    INSERT INTO public.conversation_summaries (
        tenant_id, call_id, lead_id, customer_id, summary_text, customer_intent,
        objections_raised, next_best_action, human_transfer_required, generated_by_model
    ) VALUES (
        :tenant_id, :call_id, :lead_id, :customer_id, :summary_text, :customer_intent,
        :objections_raised, :next_best_action, :human_transfer_required, :generated_by_model
    )
    ON CONFLICT (call_id) DO UPDATE SET
        summary_text = EXCLUDED.summary_text,
        customer_intent = EXCLUDED.customer_intent,
        objections_raised = EXCLUDED.objections_raised,
        next_best_action = EXCLUDED.next_best_action,
        human_transfer_required = EXCLUDED.human_transfer_required,
        generated_by_model = EXCLUDED.generated_by_model,
        generated_at = NOW(),
        updated_at = NOW()
    RETURNING id
    """
)


class VoiceCallRepository:
    """Tenant-scoped writes for one voice call's transcript and intelligence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append_message(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        sequence_number: int,
        speaker: str,
        message_text: str | None = None,
        message_type: str = "speech",
        intent: str | None = None,
        language: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Append one transcript row. Idempotent on (call_id, sequence_number).

        Returns None on a duplicate sequence number -- a reconnecting media
        stream replaying a turn must not double the transcript.
        """
        result = await self.session.execute(
            _INSERT_CALL_MESSAGE_SQL,
            {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "sequence_number": sequence_number,
                "speaker": speaker,
                "message_type": message_type,
                "message_text": message_text,
                "intent": intent,
                "language": language,
                "tool_name": tool_name,
                "tool_args": json.dumps(tool_args, default=str) if tool_args else None,
                "tool_result": json.dumps(tool_result, default=str) if tool_result else None,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )
        row = result.mappings().one_or_none()
        return row["id"] if row else None

    async def upsert_agent_session(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        customer_id: UUID,
        lead_id: UUID | None,
        status: str = "active",
        current_intent: str | None = None,
        detected_language: str | None = None,
        conversation_summary: str | None = None,
        extracted_requirements: dict[str, Any] | None = None,
        ended_at: Any = None,
    ) -> UUID | None:
        """Create or update the durable agent session for this call."""
        result = await self.session.execute(
            _UPSERT_AGENT_SESSION_SQL,
            {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "status": status,
                "current_intent": current_intent,
                "detected_language": detected_language,
                "conversation_summary": conversation_summary,
                "extracted_requirements": json.dumps(
                    extracted_requirements or {}, default=str
                ),
                "ended_at": ended_at,
            },
        )
        row = result.mappings().one_or_none()
        return row["id"] if row else None

    async def upsert_conversation_summary(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        customer_id: UUID,
        lead_id: UUID | None,
        summary_text: str | None,
        customer_intent: str | None,
        objections: list[str] | None,
        next_best_action: str | None,
        human_transfer_required: bool,
        generated_by_model: str | None,
    ) -> UUID | None:
        """Store the post-call intelligence. One row per call, upserted."""
        result = await self.session.execute(
            _UPSERT_CONVERSATION_SUMMARY_SQL,
            {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "summary_text": summary_text,
                "customer_intent": customer_intent,
                "objections_raised": json.dumps(objections or [], default=str),
                "next_best_action": next_best_action,
                "human_transfer_required": human_transfer_required,
                "generated_by_model": generated_by_model,
            },
        )
        row = result.mappings().one_or_none()
        return row["id"] if row else None
