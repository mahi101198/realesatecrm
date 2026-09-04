"""A manual, no-telephony way to talk to the voice agent -- for QA only.

WHY THIS EXISTS
    Superfone is not provisioned for real inbound/outbound PSTN calls in this
    deployment yet. To exercise `VoiceAgent` end-to-end (STT -> reasoning ->
    tools -> TTS) without a phone, this module starts the exact same
    `pipeline.build_agent`/`build_session` flow `VoiceService._run` uses, but
    joins the tester into the room directly as a native LiveKit WebRTC
    participant from their browser, instead of bridging Superfone's PCMA
    stream. `bridge.py` is not involved at all: the browser's own mic/speaker
    talk to LiveKit the same way any LiveKit client does.

THE DB SHAPE IS REAL, ON PURPOSE
    A `calls` row (with real `call_jobs`/`call_attempts` parents) is created
    for every test call, using the exact tables `AgentGateway` uses for a real
    Superfone call -- so `VoiceAgent.record_outcome` and
    `AgentGateway.record_call_completed` run completely unmodified, and a test
    call shows up in the same transcript/summary views as a real one (tagged
    `provider = 'browser_test'`). Two guards keep a test call from ever being
    picked up by the real outbound dialer:
      * `max_attempts = attempt_count = 1` on the `call_jobs` row, so
        `record_call_completed`'s retry policy can only land on 'completed' or
        'failed' -- never 'retry_pending', the only status
        `app/workers/call_scheduler.py` polls for.
      * `provider = 'browser_test'` / `phone_to = 'browser-test-session'` on
        the `calls` row, so nothing mistakes it for a placed SFVoPI call.

LIFECYCLE
    `start_test_call` creates the DB rows, opens the room, and spawns a
    background task that owns the agent for the room's whole life on ITS OWN
    DB session -- never the request's, which closes the moment the HTTP
    response returns. The task ends on whichever comes first: the tester's
    browser leaving the room, an explicit `stop_test_call`, or
    `MAX_TEST_CALL_SECONDS`. `_ACTIVE` is an in-memory registry: a process
    restart silently drops any test call in flight, which is fine for a manual
    QA tool and would not be fine for the real call path.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.voice import livekit_gateway, pipeline
from app.voice.agent import VoiceAgent
from app.voice.context import CallCorrelation, VoiceCallContext, build_voice_call_context

logger = logging.getLogger(__name__)

# A forgotten browser tab must not run (and bill LLM/STT/TTS usage) forever.
MAX_TEST_CALL_SECONDS = 15 * 60


class _TestCallHandle:
    def __init__(
        self, tenant_id: UUID, task: "asyncio.Task[None]", stop_event: asyncio.Event
    ) -> None:
        self.tenant_id = tenant_id
        self.task = task
        self.stop_event = stop_event


# In-memory only -- see module docstring. Keyed by `calls.id`.
_ACTIVE: dict[UUID, _TestCallHandle] = {}


async def start_test_call(
    session: AsyncSession, *, tenant_id: UUID, lead_id: UUID, tester_identity: str
) -> dict[str, Any]:
    """Create a test call for `lead_id` and start the AI agent in its room.

    Returns the LiveKit join info (`room_name`, `livekit_url`, `token`) the
    caller hands straight to a browser LiveKit client. Raises `AppError` (never
    a bare exception) on anything that stops the call from starting, so the
    router can turn it into a clean HTTP error instead of a 500.
    """
    blocked = livekit_gateway.voice_disabled_reason() or pipeline.pipeline_unavailable_reason()
    if blocked:
        raise AppError(message=blocked, code="VOICE_AGENT_UNAVAILABLE", status_code=503)

    lead_row = (
        await session.execute(
            text(
                "SELECT customer_id FROM public.leads "
                "WHERE id = :lead_id AND tenant_id = :tenant_id"
            ),
            {"lead_id": lead_id, "tenant_id": tenant_id},
        )
    ).mappings().one_or_none()
    if lead_row is None:
        raise NotFoundError(message=f"Lead '{lead_id}' not found.", code="LEAD_NOT_FOUND")
    customer_id = lead_row["customer_id"]

    job_row = (
        await session.execute(
            text(
                """
                INSERT INTO public.call_jobs (
                    tenant_id, lead_id, customer_id, job_type, status,
                    attempt_count, max_attempts, started_at
                ) VALUES (
                    :tenant_id, :lead_id, :customer_id, 'initial_lead_call',
                    'calling', 1, 1, NOW()
                )
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "lead_id": lead_id, "customer_id": customer_id},
        )
    ).mappings().one()
    call_job_id = job_row["id"]

    att_row = (
        await session.execute(
            text(
                """
                INSERT INTO public.call_attempts (
                    tenant_id, lead_id, customer_id, call_job_id, attempt_number,
                    status, started_at
                ) VALUES (
                    :tenant_id, :lead_id, :customer_id, :call_job_id, 1,
                    'in_progress', NOW()
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "call_job_id": call_job_id,
            },
        )
    ).mappings().one()
    call_attempt_id = att_row["id"]

    calls_row = (
        await session.execute(
            text(
                """
                INSERT INTO public.calls (
                    tenant_id, lead_id, customer_id, direction, provider,
                    provider_call_id, phone_from, phone_to, status
                ) VALUES (
                    :tenant_id, :lead_id, :customer_id, 'outbound', 'browser_test',
                    :provider_call_id, 'browser-test', 'browser-test-session',
                    'in_progress'::public.call_status
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "provider_call_id": f"browser-test-{uuid4().hex}",
            },
        )
    ).mappings().one()
    call_id = calls_row["id"]

    await session.execute(
        text(
            "UPDATE public.call_jobs SET call_id = :call_id, updated_at = NOW() "
            "WHERE id = :call_job_id"
        ),
        {"call_id": call_id, "call_job_id": call_job_id},
    )
    await session.commit()

    correlation = CallCorrelation(
        tenant_id=tenant_id,
        call_id=call_id,
        call_job_id=call_job_id,
        call_attempt_id=call_attempt_id,
        contact_id=customer_id,
        lead_id=lead_id,
        provider_call_id="",
    )
    context = await build_voice_call_context(
        session,
        correlation,
        reason_for_call="Manual test call started from the CRM dashboard.",
    )

    room_name = livekit_gateway.room_name_for_call(call_job_id, call_attempt_id)
    if not await livekit_gateway.ensure_room(room_name, metadata=context.as_room_metadata()):
        raise AppError(
            message="Could not create the LiveKit room for this test call.",
            code="ROOM_CREATE_FAILED",
            status_code=502,
        )

    tester_token = livekit_gateway.build_access_token(
        room_name=room_name, identity=tester_identity, can_publish=True, can_subscribe=True
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(_run_test_call(context, room_name, stop_event))
    _ACTIVE[call_id] = _TestCallHandle(tenant_id, task, stop_event)

    return {
        "call_id": str(call_id),
        "room_name": room_name,
        "livekit_url": settings.LIVEKIT_URL,
        "token": tester_token,
        "identity": tester_identity,
    }


async def stop_test_call(call_id: UUID, *, tenant_id: UUID) -> bool:
    """Signal a running test call to end and wait for teardown to finish.

    Returns False for an unknown/already-finished call id, OR one that belongs
    to a different tenant, rather than raising: the tester hitting "hang up"
    twice, or after the agent already ended the call itself, is not an error --
    and a call id from another tenant must look exactly like an unknown one,
    never leak whether it exists.
    """
    handle = _ACTIVE.get(call_id)
    if handle is None or handle.tenant_id != tenant_id:
        return False
    handle.stop_event.set()
    try:
        await asyncio.wait_for(asyncio.shield(handle.task), timeout=20)
    except TimeoutError:
        logger.warning(f"Test call {call_id} did not finish tearing down within 20s.")
    return True


async def _run_test_call(
    context: VoiceCallContext, room_name: str, stop_event: asyncio.Event
) -> None:
    """Own the agent's side of one test room for its whole life.

    Mirrors `VoiceService._run`/`_finish` deliberately -- same
    `http_context.open()` wrapper, same teardown-before-summarise ordering --
    but on a dedicated session, and waiting on `stop_event`/a room
    disconnect/a timeout instead of pumping a Superfone media socket.
    """
    from app.db.session import async_session_factory

    factory = async_session_factory
    if factory is None:  # pragma: no cover -- lifespan guarantees this in prod
        logger.error(f"Cannot run test call {context.call_id}: DB engine not initialised.")
        _ACTIVE.pop(context.call_id, None)
        return

    async with factory() as session:
        conversation = VoiceAgent(session, context)
        try:
            await _join_and_converse(context, room_name, conversation, stop_event)
        except Exception as exc:  # noqa: BLE001 -- a test call must still record what it can
            logger.error(f"Test call {context.call_id} failed: {exc!s}")

        await livekit_gateway.delete_room(room_name)
        await conversation.flush_pending_writes()

        result = await conversation.summarise()
        try:
            await conversation.record_outcome(result)
            await session.commit()
        except AppError as exc:
            await session.rollback()
            logger.warning(f"Could not record test call outcome ({room_name}): {exc.message}")
        except Exception as exc:  # noqa: BLE001 -- never let teardown raise further
            await session.rollback()
            logger.error(f"Unexpected failure recording test call outcome ({room_name}): {exc!s}")

    _ACTIVE.pop(context.call_id, None)


async def _join_and_converse(
    context: VoiceCallContext,
    room_name: str,
    conversation: VoiceAgent,
    stop_event: asyncio.Event,
) -> None:
    from livekit import rtc
    from livekit.agents.utils import http_context

    async with http_context.open():
        agent = pipeline.build_agent(context, conversation)
        agent_session = pipeline.build_session()
        agent_room = rtc.Room()

        disconnect_signal = asyncio.Event()

        def _on_participant_disconnected(participant: Any) -> None:
            # Only two identities are ever in this room; anything that is not
            # the agent leaving means the tester left.
            if getattr(participant, "identity", None) != livekit_gateway.AGENT_PARTICIPANT_IDENTITY:
                disconnect_signal.set()

        agent_room.on("participant_disconnected", _on_participant_disconnected)

        await agent_room.connect(
            settings.LIVEKIT_URL,
            livekit_gateway.build_access_token(
                room_name=room_name, identity=livekit_gateway.AGENT_PARTICIPANT_IDENTITY
            ),
        )
        try:
            await agent_session.start(agent=agent, room=agent_room)
            agent_session.say(await conversation.open())

            stop_task = asyncio.ensure_future(stop_event.wait())
            disconnect_task = asyncio.ensure_future(disconnect_signal.wait())
            try:
                _done, pending = await asyncio.wait(
                    {stop_task, disconnect_task},
                    timeout=MAX_TEST_CALL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            finally:
                for task in (stop_task, disconnect_task):
                    if not task.done():
                        task.cancel()
        finally:
            try:
                await agent_session.aclose()
            finally:
                await agent_room.disconnect()
