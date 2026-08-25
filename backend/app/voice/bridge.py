"""The media bridge: Superfone's PSTN leg <-> a LiveKit room.

THE SHAPE OF THE PROBLEM
    Superfone's SFVoPI answer webhook returns Stream JSON (see
    `app/webhooks/superfone/schemas.py::StreamConfig`) telling Superfone to open
    a bidirectional WebSocket to `VOICE_AGENT_STREAM_URL`, carrying the live
    call as PCMA @ 8 kHz, `direction: BOTH`. Superfone is the WebSocket CLIENT;
    we are the server. LiveKit, meanwhile, wants linear PCM16 in an
    `rtc.AudioSource` published as a track. This class is the pump in between:

        Superfone ws  --PCMA-->  decode  --PCM16-->  published track  -> agent
        Superfone ws  <--PCMA--  encode  <--PCM16--  subscribed track <- agent

ASSUMPTION, CLEARLY FLAGGED
    That the WebSocket carries BARE binary PCMA frames is inferred from the
    Stream JSON contract (`codec`/`sample_rate`/`direction` and nothing else),
    not from a traced Superfone implementation or a captured session -- this
    repo has never held that socket open. `_extract_payload` therefore also
    accepts a JSON text frame with a base64 `payload`/`media.payload` field,
    which is the other common telephony-vendor shape, and logs-and-drops
    anything else rather than crashing the call. If real traffic turns out to be
    framed differently, `_extract_payload` and `_encode_outbound` are the two
    functions that change; nothing else in the package knows the wire format.

WHY THE PUMPS NEVER RAISE
    A live call is on the other end. Every failure path here logs and closes the
    bridge cleanly so `service.py` can still record an outcome through the
    gateway. An exception escaping into the websocket handler would abandon the
    call attempt in `calling` state until the stuck-job reconciler swept it.
"""

import asyncio
import base64
import binascii
import contextlib
import json
import logging
from collections.abc import MutableMapping
from typing import Any, Protocol

from app.voice.audio import SAMPLE_RATE_HZ, pcm16_to_pcma, pcma_to_pcm16
from app.voice.livekit_gateway import (
    AGENT_PARTICIPANT_IDENTITY,
    CUSTOMER_PARTICIPANT_IDENTITY,
    build_access_token,
)

logger = logging.getLogger(__name__)

NUM_CHANNELS = 1
CUSTOMER_TRACK_NAME = "pstn-inbound"


class MediaSocket(Protocol):
    """The slice of Starlette's WebSocket this bridge actually uses.

    A Protocol rather than the concrete class so the pumps can be unit tested
    against a fake socket without an ASGI server, and so nothing here depends
    on Starlette's internals.
    """

    async def receive(self) -> MutableMapping[str, Any]: ...

    async def send_bytes(self, data: bytes) -> None: ...


def extract_payload(message: MutableMapping[str, Any]) -> bytes | None:
    """Pull raw PCMA bytes out of one received WebSocket message.

    Returns None for anything that is not audio (keepalives, control frames,
    unparseable text) -- the caller skips those rather than treating them as
    silence, because feeding a control frame to the decoder would be noise on
    the customer's line.
    """
    if message.get("type") == "websocket.disconnect":
        return None

    payload = message.get("bytes")
    if payload:
        return bytes(payload)

    raw_text = message.get("text")
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    encoded = parsed.get("payload")
    if encoded is None:
        media = parsed.get("media")
        encoded = media.get("payload") if isinstance(media, dict) else None
    if not isinstance(encoded, str):
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("Media frame carried a non-base64 payload; dropping it.")
        return None


class SuperfoneLiveKitBridge:
    """Bridges one Superfone media WebSocket into one LiveKit room.

    Owns the LiveKit room connection for the CUSTOMER side only. The AI agent
    joins the same room as a separate participant (see `service.py`), which is
    what makes the room -- rather than this process -- the mixing point.
    """

    def __init__(self, websocket: MediaSocket, room_name: str, url: str) -> None:
        self.websocket = websocket
        self.room_name = room_name
        self.url = url
        self.room: Any | None = None
        self._source: Any | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = asyncio.Event()

    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Join the room as the customer leg and publish its audio track."""
        from livekit import rtc

        token = build_access_token(
            room_name=self.room_name,
            identity=CUSTOMER_PARTICIPANT_IDENTITY,
            can_publish=True,
            can_subscribe=True,
        )
        room = rtc.Room()
        await room.connect(self.url, token)

        source = rtc.AudioSource(SAMPLE_RATE_HZ, NUM_CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track(CUSTOMER_TRACK_NAME, source)
        await room.local_participant.publish_track(track)

        self.room = room
        self._source = source
        room.on("track_subscribed", self._on_track_subscribed)

    def _on_track_subscribed(self, track: Any, *_: Any) -> None:
        """Start relaying the agent's audio back down to Superfone.

        Fires for every subscribed track; only the AI agent's audio is relayed,
        so a human monitor joining the room to listen in is never echoed onto
        the customer's line.
        """
        from livekit import rtc

        if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
            return
        publication_identity = getattr(getattr(track, "participant", None), "identity", None)
        if publication_identity not in (None, AGENT_PARTICIPANT_IDENTITY):
            return
        self._tasks.append(asyncio.create_task(self._pump_outbound(track)))

    # ------------------------------------------------------------------

    async def pump_inbound(self) -> None:
        """Read Superfone -> decode -> publish into the room. Runs until close."""
        from livekit import rtc

        while not self._closed.is_set():
            try:
                message = await self.websocket.receive()
            except Exception as exc:  # noqa: BLE001 -- the socket died; end the bridge
                logger.info(f"Media socket for room {self.room_name} closed: {exc!s}")
                break
            if message.get("type") == "websocket.disconnect":
                break

            payload = extract_payload(message)
            if not payload:
                continue
            pcm = pcma_to_pcm16(payload)
            if self._source is None:
                continue
            frame = rtc.AudioFrame(
                data=pcm,
                sample_rate=SAMPLE_RATE_HZ,
                num_channels=NUM_CHANNELS,
                samples_per_channel=len(pcm) // 2,
            )
            try:
                await self._source.capture_frame(frame)
            except Exception as exc:  # noqa: BLE001 -- one bad frame is not a dead call
                logger.warning(f"Dropped an inbound audio frame: {exc!s}")

        self._closed.set()

    async def _pump_outbound(self, track: Any) -> None:
        """Read the agent's track -> encode -> write back down the socket."""
        from livekit import rtc

        stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE_HZ, num_channels=NUM_CHANNELS
        )
        try:
            async for event in stream:
                if self._closed.is_set():
                    break
                frame = getattr(event, "frame", None)
                if frame is None:
                    continue
                try:
                    await self.websocket.send_bytes(pcm16_to_pcma(bytes(frame.data)))
                except Exception as exc:  # noqa: BLE001 -- socket gone, stop relaying
                    logger.info(f"Media socket write failed for {self.room_name}: {exc!s}")
                    self._closed.set()
                    break
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Stop both pumps and leave the room. Safe to call more than once."""
        self._closed.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._source is not None:
            with contextlib.suppress(Exception):
                await self._source.aclose()
            self._source = None
        if self.room is not None:
            with contextlib.suppress(Exception):
                await self.room.disconnect()
            self.room = None
