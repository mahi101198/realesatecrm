"""G.711 A-law (PCMA) <-> linear PCM16 codec.

WHY THIS EXISTS AT ALL
    Superfone's SFVoPI media stream is raw PCMA at 8000 Hz (see
    `settings.VOICE_AGENT_STREAM_CODEC` / `_SAMPLE_RATE`). LiveKit's
    `rtc.AudioFrame` carries signed 16-bit little-endian linear PCM. Something
    has to transcode, and the stdlib's `audioop` -- which had `lin2alaw` /
    `alaw2lin` -- was removed in Python 3.13. So the conversion is implemented
    here, from the CCITT G.711 reference algorithm, as pure-Python lookup
    tables built once at import.

WHY LOOKUP TABLES
    A-law is a bijection on 8 bits in one direction and a 13-bit quantisation
    in the other, so both directions collapse to table lookups. The tables are
    built at import (256 entries + 65536 entries, ~64 KB) rather than computed
    per sample: a 20 ms telephony frame is 160 samples and arrives 50x per
    second per call, so per-sample branching would be the hot loop.

NO RESAMPLING HAPPENS HERE
    8 kHz in, 8 kHz out. LiveKit accepts an 8 kHz audio source directly, so the
    bridge does not resample; if a future STT plugin demands 16 kHz, resample at
    the pipeline seam (`rtc.AudioResampler`), not here.
"""

import sys
from array import array

_SEG_AEND = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)

# Frame geometry for PCMA @ 8 kHz: 20 ms == 160 samples == 160 A-law bytes.
SAMPLE_RATE_HZ = 8000
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE_HZ * FRAME_DURATION_MS // 1000


def _alaw_to_linear(a_val: int) -> int:
    """CCITT G.711 `alaw2linear`. Returns a signed 16-bit sample."""
    a_val ^= 0x55
    t = (a_val & 0x0F) << 4
    seg = (a_val & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if (a_val & 0x80) else -t


def _linear_to_alaw(pcm_val: int) -> int:
    """CCITT G.711 `linear2alaw`. Takes a signed 16-bit sample."""
    pcm_val >>= 3  # 16-bit -> 13-bit, as the reference algorithm requires
    if pcm_val >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm_val = -pcm_val - 1

    seg = 8
    for i, end in enumerate(_SEG_AEND):
        if pcm_val <= end:
            seg = i
            break
    if seg >= 8:
        return 0x7F ^ mask

    aval = seg << 4
    aval |= (pcm_val >> 1) & 0x0F if seg < 2 else (pcm_val >> seg) & 0x0F
    return aval ^ mask


# byte -> 2-byte little-endian PCM16
_DECODE_TABLE: tuple[bytes, ...] = tuple(
    (_alaw_to_linear(b) & 0xFFFF).to_bytes(2, "little") for b in range(256)
)
# unsigned 16-bit sample value -> A-law byte
_ENCODE_TABLE: bytes = bytes(
    _linear_to_alaw(v - 0x10000 if v >= 0x8000 else v) for v in range(0x10000)
)


def pcma_to_pcm16(payload: bytes) -> bytes:
    """Decode A-law telephony bytes to signed 16-bit LE PCM (2x the length)."""
    table = _DECODE_TABLE
    return b"".join([table[b] for b in payload])


def pcm16_to_pcma(payload: bytes) -> bytes:
    """Encode signed 16-bit LE PCM to A-law (half the length).

    An odd trailing byte cannot be a whole sample; it is dropped rather than
    guessed, because a half sample fed back into the table would be a click on
    the customer's phone.
    """
    usable = len(payload) - (len(payload) % 2)
    samples = array("H")
    samples.frombytes(payload[:usable])
    if samples.itemsize != 2:  # pragma: no cover -- 'H' is 2 bytes everywhere
        raise RuntimeError("array('H') is not 16-bit on this platform.")
    if sys.byteorder != "little":  # pragma: no cover -- CI/prod are LE
        samples.byteswap()
    table = _ENCODE_TABLE
    return bytes([table[v] for v in samples])
