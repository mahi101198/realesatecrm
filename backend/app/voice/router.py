"""The media WebSocket endpoint Superfone connects OUT to.

HOW SUPERFONE GETS HERE (traced, then one inferred step)
    TRACED: `AgentGateway._place_sfvopi_call` registers answer/ring/hangup URLs
    with SFVoPI. On answer, `app/webhooks/superfone/router.py` returns the
    Stream JSON built by `SuperfoneWebhookService.build_stream_response`, whose
    `url` is `settings.VOICE_AGENT_STREAM_URL`.
    INFERRED: that Superfone then opens a WebSocket to that URL and streams the
    call as bare binary PCMA frames. The Stream JSON contract is the only
    evidence -- there is no captured session in this repo. See `bridge.py` for
    the frame-shape assumption and where to change it.

CORRELATION
    The Stream JSON `url` is a single static value, so it cannot by itself say
    which call is arriving. `build_stream_response` now appends the SFVoPI
    `request_uuid`/`call_uuid` as a `call` query parameter (that value is what
    `AgentGateway._place_sfvopi_call` already stored as
    `calls.provider_call_id`), and this endpoint reads it back. A stream with no
    `call` parameter, or one naming a call we did not place, is accepted and
    immediately closed -- never bridged.

AUTH
    Same posture as the SFVoPI webhooks, and for the same reason: SFVoPI
    supports no signature and no configurable auth header, so the shared secret
    rides in the URL we registered. `verify_sfvopi_webhook_token` is reused
    verbatim rather than reimplemented. On a bad token the socket is closed with
    1008 BEFORE `accept()`, so an unauthenticated peer never gets a session.
"""

import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.exceptions import AppError
from app.db import session as db_session
from app.voice.service import VoiceService
from app.webhooks.superfone.security import verify_sfvopi_webhook_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["Voice Agent"])

# RFC 6455 close codes used here.
WS_POLICY_VIOLATION = 1008
WS_NORMAL_CLOSURE = 1000


@router.websocket("/stream")
async def voice_media_stream(
    websocket: WebSocket,
    call: Annotated[str | None, Query(description="SFVoPI request_uuid/call_uuid")] = None,
    token: Annotated[str | None, Query(description="Shared secret from the Stream URL")] = None,
) -> None:
    """Bidirectional PCMA media stream for one Superfone call.

    Deliberately NOT declared with `Depends(get_db_session)`: that dependency's
    session would be scoped to the whole websocket lifetime including teardown
    ordering we do not control, and a call lasts minutes. The session is opened
    explicitly here instead, and closed in `finally` no matter how the call ends.
    """
    try:
        verify_sfvopi_webhook_token(token)
    except AppError:
        logger.warning("Rejected a voice media stream with a bad or missing token.")
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    await websocket.accept()

    if not call:
        logger.warning("Voice media stream arrived without a 'call' correlation parameter.")
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    # Preflight BEFORE a DB session is opened. A deployment with no LiveKit,
    # no Anthropic key or no STT/TTS provider would otherwise hold a pooled
    # connection for a stream it was always going to refuse. `VoiceService`
    # re-checks this itself, so a direct caller cannot skip the gate.
    blocked = VoiceService.preflight()
    if blocked:
        logger.info(f"Voice agent disabled, closing media stream for {call}: {blocked}")
        await websocket.close(code=WS_NORMAL_CLOSURE)
        return

    # Read through the module, never `from ... import async_session_factory`:
    # the factory is a module-level global that `init_db_engine` REBINDS at
    # startup, so a name imported at import time would be permanently None.
    factory = db_session.async_session_factory
    if factory is None:  # pragma: no cover -- lifespan guarantees this
        logger.error("Voice media stream arrived before the DB engine was initialised.")
        await websocket.close(code=WS_NORMAL_CLOSURE)
        return

    async with factory() as session:
        try:
            result = await VoiceService(session).handle_media_stream(websocket, call)
            logger.info(f"Voice media stream for {call} finished: {result.as_dict()}")
        except WebSocketDisconnect:
            # The customer hung up mid-teardown. Everything the service could
            # record, it already recorded; there is nothing to salvage.
            logger.info(f"Voice media stream for {call} disconnected.")
        except Exception as exc:  # noqa: BLE001 -- a socket handler must not 500
            await session.rollback()
            logger.error(f"Voice media stream for {call} failed: {exc!s}")
        finally:
            await session.close()

    # Already closed by the peer or by the bridge? Closing twice is not an
    # error worth surfacing.
    with contextlib.suppress(RuntimeError):
        await websocket.close(code=WS_NORMAL_CLOSURE)
