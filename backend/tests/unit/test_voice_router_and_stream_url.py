"""The Superfone seam: the Stream URL we advertise, and the socket that answers.

`build_stream_response` is the ONLY place this repo tells Superfone where to
send call audio, so the correlation parameter appended there is what makes the
whole voice layer addressable. The websocket route is the other half.

Note the standing assumption, flagged here as it is in `app/voice/bridge.py`:
that Superfone dials this URL and streams bare binary PCMA is inferred from the
Stream JSON contract, not from a captured session.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.main import app
from app.voice import pipeline
from app.voice.router import (
    WS_NORMAL_CLOSURE,
    WS_POLICY_VIOLATION,
    voice_media_stream,
)
from app.webhooks.superfone.service import SuperfoneWebhookService

STREAM_PATH = "/api/v1/voice/stream"


@pytest.fixture(autouse=True)
def _stream_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        settings,
        "VOICE_AGENT_STREAM_URL",
        "wss://crm.example.com/api/v1/voice/stream?token=shh",
        raising=False,
    )
    monkeypatch.setattr(
        settings, "SUPERFONE_WEBHOOK_SHARED_SECRET", SecretStr("shh"), raising=False
    )


@pytest.fixture(autouse=True)
def _no_providers():
    """The voice layer is OFF for these tests unless a test says otherwise."""
    pipeline.register_stt_factory(None)
    pipeline.register_tts_factory(None)
    yield
    pipeline.register_stt_factory(None)
    pipeline.register_tts_factory(None)


# ---------------------------------------------------------------------------
# The Stream JSON we hand Superfone
# ---------------------------------------------------------------------------


def _stream_url_for(payload: dict | None) -> str:
    return SuperfoneWebhookService(None).build_stream_response(payload).stream.url


def test_stream_url_names_the_call_so_the_socket_can_correlate_it() -> None:
    """The configured URL is static; without this parameter the media socket
    cannot know which call arrived."""
    url = _stream_url_for({"request_uuid": "req-abc", "call_uuid": "call-xyz"})
    query = parse_qs(urlparse(url).query)
    assert query["call"] == ["req-abc"]
    # The pre-existing auth token must survive the append.
    assert query["token"] == ["shh"]


def test_call_uuid_is_used_when_no_request_uuid_is_present() -> None:
    url = _stream_url_for({"call_uuid": "call-xyz"})
    assert parse_qs(urlparse(url).query)["call"] == ["call-xyz"]


def test_a_url_without_a_query_string_gets_a_question_mark() -> None:
    settings.VOICE_AGENT_STREAM_URL = "wss://crm.example.com/stream"
    try:
        url = _stream_url_for({"request_uuid": "req-1"})
        assert url == "wss://crm.example.com/stream?call=req-1"
    finally:
        settings.VOICE_AGENT_STREAM_URL = ""


def test_no_payload_leaves_the_configured_url_untouched() -> None:
    """The pure no-argument form still works, unchanged."""
    assert _stream_url_for(None) == "wss://crm.example.com/api/v1/voice/stream?token=shh"


def test_a_payload_with_no_ids_appends_nothing() -> None:
    assert _stream_url_for({"unrelated": "field"}) == (
        "wss://crm.example.com/api/v1/voice/stream?token=shh"
    )


def test_stream_config_still_advertises_pcma_at_8k_both_ways() -> None:
    stream = SuperfoneWebhookService(None).build_stream_response({"request_uuid": "r"}).stream
    assert stream.codec == "PCMA"
    assert stream.sample_rate == 8000
    assert stream.direction == "BOTH"


# ---------------------------------------------------------------------------
# The websocket endpoint
# ---------------------------------------------------------------------------


class FakeSocket:
    """A stand-in for Starlette's WebSocket.

    The route handler is exercised by calling it directly rather than through
    `TestClient`, which this project cannot use: `starlette.testclient` now
    warns that httpx is deprecated in favour of httpx2, and pytest is
    configured with `filterwarnings = ["error"]`, so merely importing it fails.
    Calling the handler is also a sharper test -- it asserts on the close codes
    the handler chooses, not on what a client library reports.
    """

    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: int | None = None
        self.received = 0

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = WS_NORMAL_CLOSURE) -> None:
        if self.closed_with is None:
            self.closed_with = code

    async def receive(self) -> dict:
        self.received += 1
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
        raise AssertionError("Nothing should be sent on a refused stream.")


def test_the_route_is_mounted_where_the_env_example_says_it_is() -> None:
    """`.env.example` tells operators to point VOICE_AGENT_STREAM_URL at this
    exact path, so the path is part of the deployment contract.

    Resolved by endpoint name rather than by walking `app.routes`: this FastAPI
    version wraps included routers in an opaque `_IncludedRouter` node, so the
    flat route list does not contain the mounted path.
    """
    assert app.url_path_for("voice_media_stream") == STREAM_PATH


@pytest.mark.parametrize("token", [None, "", "wrong-secret"])
async def test_an_unauthenticated_stream_is_closed_before_it_is_accepted(
    token: str | None,
) -> None:
    """SFVoPI supports no signature, so the URL token is the whole defence --
    and an unauthenticated peer must never reach an accepted session."""
    socket = FakeSocket()
    await voice_media_stream(socket, call="req-1", token=token)

    assert socket.accepted is False
    assert socket.closed_with == WS_POLICY_VIOLATION


async def test_a_valid_token_without_a_call_id_is_accepted_then_closed() -> None:
    """Accepted first so Superfone sees a clean close rather than a handshake
    failure it might retry against."""
    socket = FakeSocket()
    await voice_media_stream(socket, call=None, token="shh")

    assert socket.accepted is True
    assert socket.closed_with == WS_POLICY_VIOLATION
    assert socket.received == 0


async def test_a_disabled_voice_layer_closes_cleanly_and_reads_no_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core degradation guarantee: with LiveKit unconfigured the socket is
    accepted, closed normally, and not one frame of the call is consumed."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    socket = FakeSocket()

    await voice_media_stream(socket, call="req-1", token="shh")

    assert socket.accepted is True
    assert socket.closed_with == WS_NORMAL_CLOSURE
    assert socket.received == 0


async def test_the_disabled_path_never_opens_a_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream we were always going to refuse must not take a pooled
    connection with it."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)

    def _explode():  # pragma: no cover -- asserted never to run
        raise AssertionError("A DB session was opened for a refused stream.")

    monkeypatch.setattr("app.db.session.async_session_factory", _explode, raising=False)
    await voice_media_stream(FakeSocket(), call="req-1", token="shh")
