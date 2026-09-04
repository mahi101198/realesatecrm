"""The ONE seam between this application and the LiveKit SDKs.

Mirrors `app/agents/llm.py`'s posture for Anthropic: nothing outside this
module imports `livekit.api`, and every entry point is gated by
`is_voice_agent_enabled()` so an unconfigured deployment degrades to "calls
happen, they just have no AI agent on them" rather than raising per call.

FAIL CLOSED, NEVER FAIL LOUD
    `is_livekit_configured()` requires all three of LIVEKIT_URL /
    LIVEKIT_API_KEY / LIVEKIT_API_SECRET. `is_voice_agent_enabled()` additionally
    requires the VOICE_AGENT_ENABLED kill-switch and a usable voice reasoning
    plane (`VOICE_LLM_MODEL` on LiveKit Inference -- NOT Anthropic, which the
    voice channel no longer uses) -- a room with no LLM behind it is a silent
    phone call, which is worse for the customer than no AI at all. Callers check
    the gate; `get_room_client()` raises `LiveKitUnavailableError` if they forget.

LAZY IMPORTS
    `livekit.api` / `livekit.rtc` are imported inside functions, not at module
    scope. Three reasons: the SDK pulls in `av`, `grpcio` and numpy (tens of MB
    and a measurable import cost) that a deployment without LiveKit should never
    pay; the unit tests can exercise naming/gating logic without the SDK
    present; and the SDK boundary stays a single patchable name per function.

ROOM NAMING
    One room per CALL ATTEMPT, not per call job: a retried job dials again and
    must not collide with the room of the attempt that failed. The name embeds
    both ids so an operator reading LiveKit's dashboard can join it straight
    back to `call_jobs` / `call_attempts` rows without a lookup table.
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.config import settings
from app.voice import llm as voice_llm

if TYPE_CHECKING:  # pragma: no cover -- typing only, never imported at runtime
    from livekit import api as lk_api

logger = logging.getLogger(__name__)

ROOM_NAME_PREFIX = "call"
# LiveKit identities are opaque strings; these two are fixed so a consumer of
# the room (or a recording) can tell the two legs apart without metadata.
CUSTOMER_PARTICIPANT_IDENTITY = "pstn-customer"
AGENT_PARTICIPANT_IDENTITY = "ai-voice-agent"

# Rooms are cheap but not free; a call attempt that never connects should not
# leave a room alive for hours.
ROOM_EMPTY_TIMEOUT_SECONDS = 300
ROOM_MAX_PARTICIPANTS = 4

_ROOM_NAME_RE = re.compile(
    rf"^{ROOM_NAME_PREFIX}-(?P<job>[0-9a-f]{{32}})-(?P<attempt>[0-9a-f]{{32}})$"
)


class VoiceLayerError(RuntimeError):
    """Base class for every failure originating in the voice package."""


class LiveKitUnavailableError(VoiceLayerError):
    """LiveKit is not configured (or the voice layer is switched off)."""


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def is_livekit_configured() -> bool:
    """True when all three LiveKit credentials are present."""
    return bool(
        settings.LIVEKIT_URL.strip()
        and settings.LIVEKIT_API_KEY.get_secret_value().strip()
        and settings.LIVEKIT_API_SECRET.get_secret_value().strip()
    )


def is_voice_agent_enabled() -> bool:
    """The single gate every voice entry point checks first.

    Requires the kill-switch, the media plane (LiveKit rooms) AND the reasoning
    plane (LiveKit Inference + VOICE_LLM_MODEL). Any one missing means we cannot
    hold a conversation, so we do not open a room at all -- the Superfone call
    proceeds exactly as it does today, minus the AI participant.

    Deliberately does NOT require GEMINI_API_KEY any more: the voice agent
    reasons through `app/voice/llm.py`, so a deployment that runs voice without
    WhatsApp needs no Gemini key at all, and gating on one would refuse a
    perfectly workable configuration.
    """
    return bool(
        settings.VOICE_AGENT_ENABLED
        and is_livekit_configured()
        and voice_llm.is_voice_llm_configured()
    )


def voice_disabled_reason() -> str | None:
    """Human-readable reason the voice layer is off, or None when it is on.

    Returned to callers/logs instead of a stack trace, because "disabled" is a
    supported configuration, not an error.
    """
    if not settings.VOICE_AGENT_ENABLED:
        return "VOICE_AGENT_ENABLED is false."
    if not is_livekit_configured():
        return "LiveKit is not configured (LIVEKIT_URL/API_KEY/API_SECRET)."
    if not voice_llm.is_voice_llm_configured():
        return "VOICE_LLM_MODEL is not configured; a voice room would have no agent."
    return None


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def room_name_for_call(call_job_id: UUID, call_attempt_id: UUID) -> str:
    """Deterministic room name for one call attempt.

    Deterministic on purpose: the websocket handler and anything that later
    wants to inspect/close the room derive the same name from the same ids
    without needing to persist it anywhere.
    """
    return f"{ROOM_NAME_PREFIX}-{call_job_id.hex}-{call_attempt_id.hex}"


def parse_room_name(room_name: str) -> tuple[UUID, UUID] | None:
    """Inverse of `room_name_for_call`; None if the name is not one of ours.

    Exists so an operator (or a webhook carrying only a room name) can get back
    to the CRM rows, and so a room we did not create is never mistaken for one
    we did.
    """
    match = _ROOM_NAME_RE.match(room_name)
    if match is None:
        return None
    return UUID(match.group("job")), UUID(match.group("attempt"))


# ---------------------------------------------------------------------------
# Server-side room + token management
# ---------------------------------------------------------------------------


def get_room_client() -> "lk_api.LiveKitAPI":
    """Build a LiveKit server API client.

    Not cached, unlike the Anthropic client: `LiveKitAPI` owns an aiohttp
    session that must be closed, and callers use it as an async context
    manager for exactly one operation.
    """
    if not is_livekit_configured():
        raise LiveKitUnavailableError("LiveKit credentials are not configured.")
    from livekit import api as lk_api

    return lk_api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY.get_secret_value(),
        api_secret=settings.LIVEKIT_API_SECRET.get_secret_value(),
    )


def build_access_token(
    *,
    room_name: str,
    identity: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Mint a JWT scoped to exactly one room and one identity.

    Grants are deliberately narrow: `room_join` for this room only, never
    `room_admin` / `room_create` / `room_list`. A leaked call token must not be
    usable against any other call.
    """
    if not is_livekit_configured():
        raise LiveKitUnavailableError("LiveKit credentials are not configured.")
    from livekit import api as lk_api

    token = (
        lk_api.AccessToken(
            api_key=settings.LIVEKIT_API_KEY.get_secret_value(),
            api_secret=settings.LIVEKIT_API_SECRET.get_secret_value(),
        )
        .with_identity(identity)
        .with_grants(
            lk_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=False,
            )
        )
    )
    if metadata:
        token = token.with_metadata(json.dumps(metadata, default=str))
    return token.to_jwt()


async def ensure_room(room_name: str, *, metadata: dict[str, Any] | None = None) -> bool:
    """Create the room if it does not exist. Returns True on success.

    Idempotent: LiveKit's CreateRoom returns the existing room rather than
    erroring, so a reconnecting media stream re-enters the same room.

    Never raises on a LiveKit-side failure. A room we could not create means
    "this call gets no AI agent", which is a degraded call -- not a reason to
    tear down a live customer conversation that Superfone is already carrying.
    """
    if not is_livekit_configured():
        return False
    from livekit import api as lk_api

    try:
        client = get_room_client()
        try:
            await client.room.create_room(
                lk_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=ROOM_EMPTY_TIMEOUT_SECONDS,
                    max_participants=ROOM_MAX_PARTICIPANTS,
                    metadata=json.dumps(metadata or {}, default=str),
                )
            )
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 -- degradation path, see docstring
        logger.error(f"Could not create LiveKit room {room_name!r}: {exc!s}")
        return False
    return True


async def delete_room(room_name: str) -> bool:
    """Best-effort teardown once the call has ended. Never raises."""
    if not is_livekit_configured():
        return False
    from livekit import api as lk_api

    try:
        client = get_room_client()
        try:
            await client.room.delete_room(lk_api.DeleteRoomRequest(room=room_name))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 -- teardown is advisory
        logger.warning(f"Could not delete LiveKit room {room_name!r}: {exc!s}")
        return False
    return True
