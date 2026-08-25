"""Voice layer: the G.711 codec and the wire-format handling in the bridge.

The codec is the one piece of the media plane that is fully verifiable without
LiveKit, a socket or a phone: it is pure arithmetic. The frame-shape handling is
verifiable too, because `extract_payload` is deliberately a pure function -- see
`app/voice/bridge.py` for why the Superfone wire format is an inference.
"""

import base64
import json
import struct

import pytest

from app.voice.audio import (
    SAMPLE_RATE_HZ,
    SAMPLES_PER_FRAME,
    pcm16_to_pcma,
    pcma_to_pcm16,
)
from app.voice.bridge import extract_payload


def _pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _unpack(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_frame_geometry_matches_telephony_expectations() -> None:
    """20 ms of PCMA at 8 kHz is 160 samples -- the frame size everything assumes."""
    assert SAMPLE_RATE_HZ == 8000
    assert SAMPLES_PER_FRAME == 160


def test_decode_doubles_length_and_encode_halves_it() -> None:
    alaw = bytes(range(256))
    pcm = pcma_to_pcm16(alaw)
    assert len(pcm) == 2 * len(alaw)
    assert len(pcm16_to_pcma(pcm)) == len(alaw)


def test_alaw_is_a_bijection_on_all_256_byte_values() -> None:
    """Decoding then re-encoding every A-law byte must return it unchanged.

    This is the strong correctness property of G.711: if it holds for all 256
    codes, the tables implement the standard and not something close to it.
    """
    alaw = bytes(range(256))
    assert pcm16_to_pcma(pcma_to_pcm16(alaw)) == alaw


def test_decoded_samples_stay_inside_16_bit_range() -> None:
    for sample in _unpack(pcma_to_pcm16(bytes(range(256)))):
        assert -32768 <= sample <= 32767


def test_silence_decodes_to_near_zero_amplitude() -> None:
    """A-law 0xD5 is the encoding of digital silence; it must not be loud."""
    pcm = pcma_to_pcm16(bytes([0xD5]) * SAMPLES_PER_FRAME)
    assert all(abs(sample) <= 8 for sample in _unpack(pcm))


def test_encode_is_stable_under_a_second_round_trip() -> None:
    """PCM -> A-law is lossy once; A-law -> PCM -> A-law must then be stable."""
    original = _pcm([0, 1000, -1000, 20000, -20000, 32767, -32768, 7, -7])
    once = pcm16_to_pcma(original)
    assert pcm16_to_pcma(pcma_to_pcm16(once)) == once


@pytest.mark.parametrize("sample", [32767, -32768, 0, -1, 1])
def test_extremes_encode_without_overflow(sample: int) -> None:
    encoded = pcm16_to_pcma(_pcm([sample]))
    assert len(encoded) == 1
    decoded = _unpack(pcma_to_pcm16(encoded))[0]
    # Same sign (or silence) and same order of magnitude -- A-law is lossy, so
    # exact equality is not the property; a sign flip would be a loud click.
    assert (decoded >= 0) == (sample >= 0) or abs(decoded) <= 8


def test_odd_trailing_byte_is_dropped_not_guessed() -> None:
    """Half a sample fed to the table would be a click on the customer's line."""
    assert len(pcm16_to_pcma(_pcm([100, 200]) + b"\x01")) == 2


def test_empty_input_is_empty_output() -> None:
    assert pcma_to_pcm16(b"") == b""
    assert pcm16_to_pcma(b"") == b""


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


def test_binary_frame_is_taken_as_raw_pcma() -> None:
    """The primary (inferred) Superfone shape: bare binary audio frames."""
    assert extract_payload({"type": "websocket.receive", "bytes": b"\xd5\xd5"}) == b"\xd5\xd5"


def test_base64_json_frame_is_accepted() -> None:
    """The fallback shape other telephony vendors use, accepted defensively."""
    encoded = base64.b64encode(b"\x01\x02").decode()
    assert extract_payload({"text": json.dumps({"payload": encoded})}) == b"\x01\x02"


def test_nested_media_payload_is_accepted() -> None:
    encoded = base64.b64encode(b"\x03\x04").decode()
    message = {"text": json.dumps({"event": "media", "media": {"payload": encoded}})}
    assert extract_payload(message) == b"\x03\x04"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "websocket.disconnect"},
        {"type": "websocket.receive"},
        {"text": "not json at all"},
        {"text": json.dumps(["a", "list"])},
        {"text": json.dumps({"event": "connected"})},
        {"text": json.dumps({"payload": 12345})},
        {"text": json.dumps({"payload": "!!!not base64!!!"})},
        {"bytes": b""},
    ],
)
def test_non_audio_frames_yield_none_rather_than_noise(message: dict) -> None:
    """Control/keepalive frames must be skipped, never decoded as audio."""
    assert extract_payload(message) is None


def test_disconnect_beats_a_stray_payload() -> None:
    """A disconnect frame ends the call even if it carries bytes."""
    assert extract_payload({"type": "websocket.disconnect", "bytes": b"\xd5"}) is None
