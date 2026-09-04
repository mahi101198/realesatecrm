"""The Voice Service: owns one call's room, bridge, agent and teardown.

RESPONSIBILITY BOUNDARY
    This is the only class in the package that knows all four moving parts
    exist. `bridge.py` knows audio, `agent.py` knows conversation, `pipeline.py`
    knows the SDK's session, `livekit_gateway.py` knows rooms -- and none of
    them know about each other. `VoiceService.handle_media_stream` is the seam
    where a Superfone WebSocket becomes a room with an AI in it.

GRACEFUL DEGRADATION IS THE DEFAULT PATH, NOT THE ERROR PATH
    Every early return below (`voice disabled`, `unknown call id`, `no STT/TTS
    provider`, `room creation failed`) closes the socket cleanly and returns a
    reason string. None of them raise, none of them touch call state. The
    Superfone call carries on exactly as it does today, and completion recording
    stays wherever it is today (the `POST /agent/calls/.../complete` endpoint in
    `app/agent/router.py`). That is the whole "AI failure must never corrupt CRM
    state" contract from spec section 20: the voice layer can fail totally and
    the CRM cannot tell the difference from "voice was never installed".

SESSION LIFETIME -- A KNOWN SCALING COUPLING
    One `AsyncSession` is held for the duration of a call and committed at turn
    boundaries, mirroring how `WhatsAppAgent` takes a session. A call lasts
    minutes, so N concurrent AI calls hold N pooled connections; DB_POOL_SIZE
    (default 10) therefore bounds concurrent AI calls just as
    `CallOrchestrator.max_concurrent_calls` (also 10) bounds concurrent dials.
    Raise both together, or move to a session-per-turn, before scaling past that.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.voice import livekit_gateway, pipeline
from app.voice.agent import VoiceAgent
from app.voice.bridge import MediaSocket, SuperfoneLiveKitBridge
from app.voice.context import (
    VoiceCallContext,
    build_voice_call_context,
    reason_for_job_type,
    resolve_call_by_provider_id,
)

logger = logging.getLogger(__name__)


class VoiceSessionResult:
    """What happened to one media stream. Never an exception."""

    def __init__(self, *, started: bool, reason: str | None = None, **extra: Any) -> None:
        self.started = started
        self.reason = reason
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"started": self.started, "reason": self.reason, **self.extra}


class VoiceService:
    """Runs the AI voice agent for one Superfone media stream."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    @staticmethod
    def preflight() -> str | None:
        """Reason the voice layer cannot run right now, or None.

        Checked before a single byte is read so an unconfigured deployment
        never accepts audio it cannot do anything with.
        """
        return livekit_gateway.voice_disabled_reason() or pipeline.pipeline_unavailable_reason()

    async def prepare(self, provider_call_id: str) -> VoiceCallContext | None:
        """Resolve the call and build its briefing, or None if unknown to us."""
        resolved = await resolve_call_by_provider_id(self.session, provider_call_id)
        if resolved is None:
            return None
        correlation, extras = resolved
        return await build_voice_call_context(
            self.session,
            correlation,
            reason_for_call=reason_for_job_type(extras.get("job_type")),
        )

    # ------------------------------------------------------------------
    # The live call
    # ------------------------------------------------------------------

    async def handle_media_stream(
        self, websocket: MediaSocket, provider_call_id: str
    ) -> VoiceSessionResult:
        """Bridge one Superfone media stream into a LiveKit room with an agent.

        Returns rather than raises in every branch; the caller's only job after
        this is to close the socket.
        """
        blocked = self.preflight()
        if blocked:
            logger.info(f"Voice agent not started for call {provider_call_id}: {blocked}")
            return VoiceSessionResult(started=False, reason=blocked)

        context = await self.prepare(provider_call_id)
        if context is None:
            logger.warning(
                f"Media stream for unknown provider call id {provider_call_id!r}; "
                "refusing to bridge it."
            )
            return VoiceSessionResult(started=False, reason="UNKNOWN_CALL")

        room_name = livekit_gateway.room_name_for_call(
            context.call_job_id, context.call_attempt_id
        )
        if not await livekit_gateway.ensure_room(
            room_name, metadata=context.as_room_metadata()
        ):
            return VoiceSessionResult(started=False, reason="ROOM_CREATE_FAILED")

        conversation = VoiceAgent(self.session, context)
        bridge = SuperfoneLiveKitBridge(websocket, room_name, settings.LIVEKIT_URL)
        try:
            await bridge.connect()
            await self._run(bridge, conversation, context, room_name)
        except Exception as exc:  # noqa: BLE001 -- a live call; degrade, don't crash
            logger.error(f"Voice session failed for room {room_name}: {exc!s}")
            return await self._finish(
                conversation,
                bridge,
                room_name,
                degraded_reason="VOICE_SESSION_ERROR",
            )
        return await self._finish(conversation, bridge, room_name)

    async def _run(
        self,
        bridge: SuperfoneLiveKitBridge,
        conversation: VoiceAgent,
        context: VoiceCallContext,
        room_name: str,
    ) -> None:
        """Start the SDK agent in the room, then pump audio until hangup.

        VERIFIED LIVE against the real LiveKit Inference gateway (Aug 2026): the
        LLM call, and separately a TTS synth call, both succeed against the
        credentials/models this deployment is configured with.

        WHY THIS IS WRAPPED IN `http_context.open()`
            `livekit.agents.inference.STT`/`TTS` lazily fetch their aiohttp
            session from `livekit.agents.utils.http_context`, which is normally
            opened once per job by the standard `agents.cli.run_app` worker
            process. This codebase never runs that worker -- the agent is
            started manually, right here, inside a FastAPI websocket handler --
            so without this wrapper every STT/TTS call raises
            `APIConnectionError: Attempted to use an http session outside of a
            job context` the instant the agent tries to listen or speak
            (reproduced against the live gateway; fixed by this exact wrapper,
            which is the SDK's own documented pattern for running plugins
            outside the worker). The LLM client is unaffected -- it does not use
            `http_context` -- which is why a bare LLM call alone would not have
            caught this.
        """
        from livekit import rtc
        from livekit.agents.utils import http_context

        async with http_context.open():
            agent = pipeline.build_agent(context, conversation)
            agent_session = pipeline.build_session()

            agent_room = rtc.Room()
            await agent_room.connect(
                settings.LIVEKIT_URL,
                livekit_gateway.build_access_token(
                    room_name=room_name,
                    identity=livekit_gateway.AGENT_PARTICIPANT_IDENTITY,
                ),
            )
            try:
                await agent_session.start(agent=agent, room=agent_room)
                _wire_diagnostics(agent_session, room_name)
                # conversation.open() only computes the line and records it to
                # the transcript/call_messages -- it never touches TTS. Without
                # this `.say()` the customer hears silence: the greeting was
                # decided, never spoken. `.say()` is a plain (non-async) call:
                # it hands the line to TTS and returns a SpeechHandle
                # immediately, it does not block until playout finishes.
                agent_session.say(await conversation.open())
                await bridge.pump_inbound()
            finally:
                try:
                    await agent_session.aclose()
                finally:
                    await agent_room.disconnect()

    async def _finish(
        self,
        conversation: VoiceAgent,
        bridge: SuperfoneLiveKitBridge,
        room_name: str,
        *,
        degraded_reason: str | None = None,
    ) -> VoiceSessionResult:
        """Tear the media down, then record the outcome through the gateway.

        Teardown happens FIRST so a slow LLM summary cannot hold a LiveKit room
        (and a Superfone socket) open after the customer has hung up.

        The outcome write is wrapped: `record_call_completed` legitimately
        raises when the job is already terminal (for instance if the existing
        `POST /agent/calls/{id}/complete` endpoint got there first), and losing
        that race must not be logged as a voice failure worth alerting on.
        """
        await bridge.aclose()
        await livekit_gateway.delete_room(room_name)
        # Transcript writes are fire-and-forget during the call (see
        # `VoiceAgent._record`) so they never delay a spoken turn; this is the
        # one point that waits for them, before summarise/record_outcome run on
        # a different session and before the process can move on.
        await conversation.flush_pending_writes()

        result = await conversation.summarise()
        if degraded_reason:
            result["degraded"] = True
        try:
            recorded = await conversation.record_outcome(
                result, termination_reason=degraded_reason
            )
            await self.session.commit()
        except AppError as exc:
            await self.session.rollback()
            logger.warning(
                f"Could not record voice call outcome for room {room_name}: "
                f"{exc.code}: {exc.message}"
            )
            return VoiceSessionResult(started=True, reason="OUTCOME_NOT_RECORDED")
        except Exception as exc:  # noqa: BLE001 -- never propagate into the socket
            await self.session.rollback()
            logger.error(f"Unexpected failure recording voice outcome ({room_name}): {exc!s}")
            return VoiceSessionResult(started=True, reason="OUTCOME_NOT_RECORDED")

        return VoiceSessionResult(started=True, reason=degraded_reason, **recorded)


def _wire_diagnostics(agent_session: Any, room_name: str) -> None:
    """Log the SDK's own turn-taking/STT events for one call.

    UNVERIFIED IN THIS ENVIRONMENT (see pipeline.py's module docstring) meant
    the room-join path had never actually been exercised end-to-end. The first
    live call through it produced audio and a spoken greeting but never a
    second turn -- with nothing in this codebase's own logs to say whether the
    SDK ever heard the customer at all. These three events are the SDK's own
    visibility into STT/VAD/turn-detection state, and cost nothing to log:
    `user_input_transcribed` fires on every interim AND final transcript (so
    "nothing logged" means STT never produced so much as a partial guess, not
    just "no turn finished"), and the state-changed events show whether the
    session ever left `listening`.
    """

    def _on_transcript(ev: Any) -> None:
        logger.info(
            f"[{room_name}] user_input_transcribed: final={ev.is_final} "
            f"text={ev.transcript!r}"
        )

    def _on_user_state(ev: Any) -> None:
        logger.info(f"[{room_name}] user_state_changed: {ev.old_state} -> {ev.new_state}")

    def _on_agent_state(ev: Any) -> None:
        logger.info(f"[{room_name}] agent_state_changed: {ev.old_state} -> {ev.new_state}")

    agent_session.on("user_input_transcribed", _on_transcript)
    agent_session.on("user_state_changed", _on_user_state)
    agent_session.on("agent_state_changed", _on_agent_state)
