"""Local console test for the voice agent's turn-taking pipeline.

RUN (from backend/, with your own mic/speaker):
    python scripts/voice_console_dev.py console

WHY THIS EXISTS
    Debugging "why doesn't the AI respond to what I said" over the real path
    (VM -> fake-Superfone -> browser mic) is a multi-minute round trip per
    attempt: redeploy, place a call, speak, wait for teardown to read a log.
    livekit-agents' own `console` command wires a terminal's local mic/speaker
    directly into an `AgentSession` -- no LiveKit room, no Superfone, no
    browser -- while still exercising the REAL pipeline this app runs in
    production: `app.voice.pipeline.build_agent`/`build_session` (same STT/TTS
    factories, same `CrmVoiceAgent.llm_node` override) and the real
    `app.voice.agent.VoiceAgent` (same LLM seam, same tool surface). Only the
    media transport changes.

WHAT IS AND ISN'T REAL HERE
    STT/TTS/LLM calls all hit the real configured providers (LiveKit
    Inference, same as production) -- this costs real API usage, same as any
    other live test. `call_id`/`call_job_id`/`call_attempt_id` below are
    placeholder UUIDs with no matching `calls`/`call_jobs` rows, so transcript
    writes and the post-call outcome write will fail on the FK constraint --
    that failure is caught and logged (see `VoiceAgent._persist_message`/
    `record_outcome`), never fatal, and irrelevant to what this script tests.
    `tenant_id`/`lead_id`/`contact_id` ARE real (the same seed lead used for
    the VM live tests), so tool calls the model makes will hit real data.
"""

import asyncio
import logging
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()

from livekit.agents import JobContext, WorkerOptions, cli  # noqa: E402 -- must follow load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_console_dev")

# Same seed lead used for the VM live tests (tenant 00000000-...-000000000001).
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
LEAD_ID = UUID("fa8a1d0d-f9f1-46e3-b740-63271d587adc")
CUSTOMER_ID = UUID("adafd3fa-6d33-4e24-824b-8c74c076b8ae")


async def entrypoint(ctx: JobContext) -> None:
    from app.db.session import init_db_engine
    from app.voice import pipeline
    from app.voice.agent import VoiceAgent
    from app.voice.context import VoiceCallContext
    from app.voice.service import _wire_diagnostics

    blocked = pipeline.register_inference_providers()
    if blocked:
        logger.error(f"Speech providers not registered: {blocked}")
        return

    init_db_engine()
    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        context = VoiceCallContext(
            tenant_id=TENANT_ID,
            contact_id=CUSTOMER_ID,
            lead_id=LEAD_ID,
            call_id=UUID(int=1),
            call_job_id=UUID(int=1),
            call_attempt_id=UUID(int=1),
            customer_name="Console Test",
            preferred_language="hi",
            reason_for_call="Local console test -- not a real call.",
        )
        conversation = VoiceAgent(session, context)
        agent = pipeline.build_agent(context, conversation)
        agent_session = pipeline.build_session()
        _wire_diagnostics(agent_session, "console-test")

        await ctx.connect()
        await agent_session.start(agent=agent, room=ctx.room)
        agent_session.say(await conversation.open())

        # Keep the job alive until Ctrl+C; console mode's own I/O runs the
        # actual conversation loop against your mic/speaker in the background.
        await asyncio.Event().wait()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
