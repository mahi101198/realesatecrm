"""The Voice Agent: the conversation loop that runs during a live phone call.

WHAT IT IS
    A turn-taker. Given the customer's transcribed utterance plus the CRM's
    briefing, it produces one short spoken reply, and at the end of the call one
    structured outcome.

WHAT IT IS NOT
    * It is not an audio component. It never sees a byte of PCM -- `bridge.py`
      and `pipeline.py` own the media plane, and hand this class strings.
    * It does not own CRM state (spec section 4). The one place a call's
      lifecycle changes is `AgentGateway.record_call_completed`; this class
      CALLS it and never re-implements the retry policy, the job state machine
      or the CALL_COMPLETED/CALL_FAILED events that live inside it.
    * It does not write anything the customer said into the lead's requirement
      fields. Its tool surface is the same four read-only lookups the WhatsApp
      agent has, enforced twice (allowlist here, permission-less RequestContext
      from `build_agent_read_context`).
    * It never calls another agent. If a voice call should trigger a WhatsApp
      follow-up, `record_outcome` publishes a domain event and stops -- spec
      section 17 forbids agent-to-agent calls, so the seam is `public.events`
      and a future consumer, not an import.

WHICH MODEL IS BEHIND IT
    `app/voice/llm.py` -- LiveKit Inference (`settings.VOICE_LLM_MODEL`), not
    Anthropic. That is the ONLY thing about this class that changed when the
    voice channel moved off the shared Anthropic seam: the briefing, the
    read-only allowlist, the transcript, the outcome schema and the
    `record_outcome` flow are all identical, because the backend is an
    implementation detail of "produce one spoken turn". WhatsApp still reasons
    through `app/agents/llm.py`; the two are parallel, never layered.

HANDOFF
    One mechanism only: `SalesHandoffService.request_handoff`, the same one the
    WhatsApp path uses. There is no voice-specific escalation table or flow.

FAILURE POSTURE (spec section 20)
    An LLM failure mid-call degrades to a fallback line, never an exception that
    would drop a live customer -- and it is REMEMBERED (`llm_degraded`). Any
    call in which the reasoning plane failed at all ends by asking for a human
    rather than by recording a conclusion the model never actually reached:
    same principle as the orchestrator's `determine_next_action`, where an AI
    failure is never a licence to guess. The deterministic floor still runs with
    zero model output -- the call is recorded, the job transitions, the CRM
    stays consistent -- it just routes to a person instead of to a guess.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.gateway import AgentGateway
from app.agent.handoff_service import SalesHandoffService
from app.agents.security import build_agent_read_context
from app.agents.tool_args import coerce_uuid_arguments
from app.core.config import settings
from app.events.model import EventType
from app.events.publisher import publish_event
from app.voice import llm as voice_llm
from app.voice.context import VoiceCallContext
from app.voice.prompts import (
    ALLOWED_TOOL_NAMES,
    MAX_SPOKEN_REPLY_CHARS,
    OUTCOME_SCHEMA,
    OUTCOME_SYSTEM_PROMPT,
    OUTCOME_VALUES,
    READ_TOOL_SCHEMAS,
    SYSTEM_PROMPT,
    build_opening_prompt,
    build_turn_prompt,
)
from app.voice.repository import SPEAKER_AGENT, SPEAKER_CUSTOMER, VoiceCallRepository

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from uuid import UUID

    from app.core.request_context import RequestContext

logger = logging.getLogger(__name__)

# Said when the model cannot produce a turn. Deliberately content-free and
# recoverable: it buys a beat without promising anything or hanging up.
FALLBACK_REPLY = "Sorry, could you say that once more?"

# The outcome recorded when the model could not summarise the call. 'connected'
# is the honest floor: the call did connect, we just have no analysis of it.
DEFAULT_OUTCOME = "connected"


class VoiceAgent:
    """Runs one AI conversation on one call attempt."""

    # Declared at class scope rather than annotated on the assignment in
    # __init__: `RequestContext` is a TYPE_CHECKING-only import, and an
    # annotation on an attribute target inside a function body is evaluated at
    # runtime before Python 3.14's deferred annotations. This project supports
    # 3.12, so the annotation must not be in an evaluated position.
    _read_context: "RequestContext"

    def __init__(self, session: AsyncSession, context: VoiceCallContext) -> None:
        self.session = session
        self.context = context
        self.repository = VoiceCallRepository(session)
        self.gateway = AgentGateway(session)
        self.handoffs = SalesHandoffService(session)
        self.transcript: list[dict[str, str]] = []
        # Sticky for the whole call: once the reasoning plane has failed even
        # once, this call ends by asking for a human (see `summarise`). It is
        # never reset -- the customer heard a fallback line, and no later
        # successful turn un-hears it.
        self.llm_degraded = False
        self._sequence = 0
        self._started_at = datetime.now(UTC)
        self._read_context = build_agent_read_context(
            context.tenant_id, request_id=f"voice-{context.call_attempt_id}"
        )

    # ------------------------------------------------------------------
    # Turn taking
    # ------------------------------------------------------------------

    async def open(self) -> str:
        """Produce the agent's first spoken line, after the customer picks up.

        Never raises, for the same reason `respond` never raises: this runs
        inside the media session, where an exception is dead air.
        """
        try:
            spoken = await voice_llm.call_with_tools(
                system=SYSTEM_PROMPT,
                user=build_opening_prompt(self.context),
                tools=READ_TOOL_SCHEMAS,
                tool_executor=self._execute_tool,
            )
        except Exception as exc:  # noqa: BLE001 -- a live call; degrade, never raise
            logger.error(f"Voice agent could not open call {self.context.call_id}: {exc!s}")
            self.llm_degraded = True
            name = self.context.customer_name or "there"
            spoken = f"Hello {name}, this is the sales team calling about your enquiry."
        return await self._say(spoken)

    async def respond(self, utterance: str) -> str:
        """Handle one customer utterance and return what to say back.

        Never raises. A model failure is answered with `FALLBACK_REPLY` because
        the alternative -- an exception propagating into the media pipeline --
        is dead air on a live call. The catch is deliberately broad rather than
        `VoiceLLMError`-only: this is called from inside the livekit-agents
        session task, so ANY escaping exception (a transport bug, a schema
        change in the SDK) kills the media stream, not just this turn.

        The failure is also remembered, so the call ends with a human handoff
        instead of an outcome nobody actually reasoned about.
        """
        await self._record(SPEAKER_CUSTOMER, utterance)

        try:
            spoken = await voice_llm.call_with_tools(
                system=SYSTEM_PROMPT,
                user=build_turn_prompt(self.context, self.transcript, utterance),
                tools=READ_TOOL_SCHEMAS,
                tool_executor=self._execute_tool,
            )
        except Exception as exc:  # noqa: BLE001 -- see docstring
            logger.error(f"Voice agent turn failed on call {self.context.call_id}: {exc!s}")
            self.llm_degraded = True
            spoken = FALLBACK_REPLY
        return await self._say(spoken)

    async def _say(self, spoken: str) -> str:
        """Trim to speakable length, record it, return it."""
        text = spoken.strip()[:MAX_SPOKEN_REPLY_CHARS]
        await self._record(SPEAKER_AGENT, text)
        return text

    async def _record(self, speaker: str, text: str) -> None:
        """Append one turn to the in-memory transcript and to `call_messages`.

        A persistence failure is logged and swallowed: losing a transcript row
        is regrettable, dropping the customer's call over it is not acceptable.
        """
        self.transcript.append({"speaker": speaker, "text": text})
        self._sequence += 1
        try:
            await self.repository.append_message(
                tenant_id=self.context.tenant_id,
                call_id=self.context.call_id,
                sequence_number=self._sequence,
                speaker=speaker,
                message_text=text,
                language=self.context.preferred_language,
            )
        except Exception as exc:  # noqa: BLE001 -- see docstring
            logger.warning(
                f"Could not persist transcript turn {self._sequence} for call "
                f"{self.context.call_id}: {exc!s}"
            )

    # ------------------------------------------------------------------
    # Tool surface (read-only, allowlisted -- mirrors the WhatsApp agent)
    # ------------------------------------------------------------------

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one model-requested tool call.

        The allowlist is the security boundary, not a nicety: the model chooses
        the name, so the name is untrusted input. Even a write tool that slipped
        past would then hit `self._read_context`, which holds no write
        permissions -- the belt to this suspenders.
        """
        if name not in ALLOWED_TOOL_NAMES:
            logger.warning(f"Voice agent requested non-allowlisted tool {name!r}; refusing.")
            return {
                "success": False,
                "error_code": "TOOL_NOT_ALLOWED",
                "message": f"Tool '{name}' is not available to this agent.",
            }

        # Lazy, so this module does not drag the whole tool registry (and its
        # service graph) into import time.
        from app.agent.tools import TOOL_REGISTRY

        handler = TOOL_REGISTRY.get(name)
        if handler is None:  # pragma: no cover -- allowlist is a registry subset
            return {"success": False, "error_code": "UNKNOWN_TOOL", "message": name}

        coerced = coerce_uuid_arguments(arguments)
        return await handler(self._read_context, self.session, **coerced)

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    async def summarise(self) -> dict[str, Any]:
        """Ask the model for the structured call outcome.

        Returns a deterministic minimal result instead of raising when the model
        is unavailable or off-schema, so `record_outcome` always has something
        valid to persist.

        That fallback asks for a HUMAN whenever the reasoning plane failed at
        any point in the call (`llm_degraded`). 'connected' remains the honest
        floor for the outcome enum -- we cannot claim the customer wanted
        anything -- but leaving a call in which the AI broke sitting quietly in
        the CRM with no summary and no follow-up is a guess by omission. A
        person picks it up instead. A call with an empty transcript and a
        healthy model (nobody said anything) is NOT a failure and does not
        escalate.
        """
        fallback: dict[str, Any] = {
            "outcome": DEFAULT_OUTCOME,
            "summary": None,
            "customer_intent": None,
            "human_transfer_required": self.llm_degraded,
            "callback_requested": False,
            "objections": [],
            "next_best_action": (
                "The AI voice agent failed mid-call; call this customer back."
                if self.llm_degraded
                else None
            ),
            "degraded": True,
        }
        if not self.transcript:
            return fallback

        rendered = "\n".join(f"{t['speaker']}: {t['text']}" for t in self.transcript)
        try:
            parsed = await voice_llm.call_structured(
                system=OUTCOME_SYSTEM_PROMPT,
                user=f"TRANSCRIPT\n{rendered}",
                schema=OUTCOME_SCHEMA,
                schema_name="call_outcome",
            )
        except Exception as exc:  # noqa: BLE001 -- never raise into call teardown
            logger.error(f"Voice agent could not summarise call {self.context.call_id}: {exc!s}")
            self.llm_degraded = True
            fallback["human_transfer_required"] = True
            fallback["next_best_action"] = (
                "The AI could not summarise this call; review it and call back."
            )
            return fallback

        outcome = str(parsed.get("outcome") or DEFAULT_OUTCOME)
        if outcome not in OUTCOME_VALUES:
            # The gateway's strict json_schema mode constrains this, but that is
            # a per-model property we cannot enforce, and the enum is also a DB
            # enum: an unexpected value would be a 22P02 on insert. Clamp it.
            logger.warning(f"Voice agent returned unknown outcome {outcome!r}; clamping.")
            outcome = DEFAULT_OUTCOME
        parsed["outcome"] = outcome
        parsed["degraded"] = False
        if self.llm_degraded:
            # A turn failed earlier in this call. The summary is real, but the
            # conversation the customer actually had was not the one we meant to
            # have, so a person picks it up regardless of what the model concluded.
            parsed["human_transfer_required"] = True
        return parsed

    async def record_outcome(
        self,
        result: dict[str, Any],
        *,
        duration_seconds: int | None = None,
        provider_call_id: str | None = None,
        termination_reason: str | None = None,
    ) -> dict[str, Any]:
        """Persist the call's result and hand the lifecycle back to the gateway.

        Order matters and is deliberate:
          1. voice-owned artefacts (`agent_sessions`, `conversation_summaries`)
          2. the handoff, if a human is needed -- via the ONE handoff service
          3. the follow-up seam: a domain event, never a call into another agent
          4. LAST, `AgentGateway.record_call_completed`, which owns the attempt
             row, the retry policy, the job transition and the CALL_COMPLETED /
             CALL_FAILED event. Nothing above duplicates any of that.

        Everything runs on the caller's session, so a rollback discards the
        whole set together -- there is no state in which a summary exists for a
        call the gateway never completed.
        """
        outcome = str(result.get("outcome") or DEFAULT_OUTCOME)
        needs_human = bool(result.get("human_transfer_required"))
        if needs_human:
            # A requested transfer IS the outcome; the retry policy treats
            # 'human_transfer' as terminal, which is what we want.
            outcome = "human_transfer"

        elapsed = duration_seconds
        if elapsed is None:
            elapsed = max(0, int((datetime.now(UTC) - self._started_at).total_seconds()))

        summary_text = result.get("summary")

        await self.repository.upsert_agent_session(
            tenant_id=self.context.tenant_id,
            call_id=self.context.call_id,
            customer_id=self.context.contact_id,
            lead_id=self.context.lead_id,
            status="completed",
            current_intent=result.get("customer_intent"),
            detected_language=self.context.preferred_language,
            conversation_summary=summary_text,
            extracted_requirements={},
            ended_at=datetime.now(UTC),
        )
        await self.repository.upsert_conversation_summary(
            tenant_id=self.context.tenant_id,
            call_id=self.context.call_id,
            customer_id=self.context.contact_id,
            lead_id=self.context.lead_id,
            summary_text=summary_text,
            customer_intent=result.get("customer_intent"),
            objections=[str(o) for o in (result.get("objections") or [])],
            next_best_action=result.get("next_best_action"),
            human_transfer_required=needs_human,
            # The voice channel reasons through LiveKit Inference, so the model
            # stamped on the summary is VOICE_LLM_MODEL, never ANTHROPIC_MODEL.
            generated_by_model=None if result.get("degraded") else settings.VOICE_LLM_MODEL,
        )

        handoff_id: UUID | None = None
        if needs_human and self.context.lead_id is not None:
            handoff = await self.handoffs.request_handoff(
                self.context.tenant_id,
                self.context.lead_id,
                self.context.contact_id,
                reason=result.get("next_best_action") or "Requested during an AI voice call.",
                notes=summary_text,
                conversation_summary=summary_text,
            )
            handoff_id = handoff.get("id")

        if result.get("callback_requested"):
            # The seam, not a shortcut: publishing an event lets a future
            # consumer send the WhatsApp confirmation. The voice agent must not
            # reach into the WhatsApp agent or the orchestrator (spec s.17).
            await publish_event(
                self.session,
                tenant_id=self.context.tenant_id,
                event_type=EventType.CALL_REQUESTED,
                contact_id=self.context.contact_id,
                lead_id=self.context.lead_id,
                payload={
                    "source": "voice_agent",
                    "call_id": str(self.context.call_id),
                    "call_job_id": str(self.context.call_job_id),
                    "reason": "Customer asked to be called back.",
                },
            )

        completion = await self.gateway.record_call_completed(
            tenant_id=self.context.tenant_id,
            call_job_id=self.context.call_job_id,
            call_attempt_id=self.context.call_attempt_id,
            outcome=outcome,
            duration_seconds=elapsed,
            call_summary=summary_text,
            provider_call_id=provider_call_id or None,
            termination_reason=termination_reason,
        )
        return {
            "outcome": outcome,
            "handoff_id": str(handoff_id) if handoff_id else None,
            "next_status": completion.get("next_status"),
            "turns": len(self.transcript),
        }
