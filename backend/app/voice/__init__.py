"""The Voice Service and Voice Agent.

WHAT THIS PACKAGE ADDS
    An AI participant on calls this platform ALREADY places. It does not dial,
    it does not replace Superfone, and it does not change one line of the
    existing call-job state machine. Superfone still places the PSTN call
    (`AgentGateway.start_call` -> `SFVoPIClient.initiate_outbound_call`) and
    still streams that call's audio to `VOICE_AGENT_STREAM_URL`. This package
    is what finally answers on the other end of that stream:

        Superfone PSTN leg
              | PCMA @ 8 kHz over a WebSocket Superfone opens to us
              v
        app/voice/router.py        the endpoint (auth + call correlation)
              v
        app/voice/service.py       resolves the call, opens the room, runs it
              v
        app/voice/bridge.py        PCMA <-> PCM16, socket <-> LiveKit track
              v
        LiveKit room "call-<job>-<attempt>"
              ^
        app/voice/pipeline.py      livekit-agents session: STT -> us -> TTS
              ^
        app/voice/agent.py         the CRM-aware conversation + outcome

THREE HARD RULES THIS PACKAGE KEEPS
    1. It does not own CRM state. Call lifecycle changes go through
       `AgentGateway.record_call_completed` and nowhere else; transcripts and
       intelligence go into the `call_messages` / `agent_sessions` /
       `conversation_summaries` tables migration 006 already defined. No new
       table was created.
    2. It calls no other agent (spec section 17). A voice call that should
       trigger a WhatsApp follow-up publishes a domain event and stops.
    3. It degrades to nothing. With LiveKit unconfigured, VOICE_AGENT_ENABLED
       false, no Anthropic key, or no STT/TTS provider registered, every entry
       point returns a reason string and closes the socket -- calls are still
       placed, still tracked, still retried, exactly as before this package
       existed.
"""

from app.voice.livekit_gateway import (
    LiveKitUnavailableError,
    VoiceLayerError,
    is_livekit_configured,
    is_voice_agent_enabled,
    room_name_for_call,
    voice_disabled_reason,
)

__all__ = [
    "LiveKitUnavailableError",
    "VoiceLayerError",
    "is_livekit_configured",
    "is_voice_agent_enabled",
    "room_name_for_call",
    "voice_disabled_reason",
]
