"""Voice Service: the disabled-mode fallback, plus the prompt/outcome contract.

The most important assertions in this file are the boring ones: with LiveKit,
Anthropic or an STT/TTS provider missing, `handle_media_stream` must return a
reason and touch NOTHING -- no room, no CRM state, no exception. That is the
whole "the call still gets placed via Superfone, it just has no AI agent"
guarantee, and it is the only part of the media path that can be verified
without a live room and a real phone call.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.voice import pipeline
from app.voice.context import VoiceCallContext
from app.voice.prompts import (
    MAX_SPOKEN_REPLY_CHARS,
    OUTCOME_SCHEMA,
    OUTCOME_VALUES,
    SYSTEM_PROMPT,
    build_briefing,
    build_opening_prompt,
    build_turn_prompt,
)
from app.voice.service import VoiceService


@pytest.fixture(autouse=True)
def _clean_pipeline_registry():
    """Provider registration is process-global; never let it leak between tests."""
    pipeline.register_stt_factory(None)
    pipeline.register_tts_factory(None)
    yield
    pipeline.register_stt_factory(None)
    pipeline.register_tts_factory(None)


@pytest.fixture
def livekit_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://x.livekit.cloud", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "vendor/model", raising=False)


def _providers_registered() -> None:
    pipeline.register_stt_factory(lambda: MagicMock())
    pipeline.register_tts_factory(lambda: MagicMock())


def _context() -> VoiceCallContext:
    return VoiceCallContext(
        tenant_id=uuid4(),
        contact_id=uuid4(),
        lead_id=uuid4(),
        call_id=uuid4(),
        call_job_id=uuid4(),
        call_attempt_id=uuid4(),
        customer_name="Asha Rao",
        interest="3 BHK in Whitefield",
        budget="80L to 1Cr",
        conversation_summary="Asked about Palm Grove on WhatsApp.",
        reason_for_call="The customer asked to be called.",
    )


# ---------------------------------------------------------------------------
# Pipeline registration seam
# ---------------------------------------------------------------------------


def test_pipeline_is_unconfigured_until_both_providers_are_registered() -> None:
    assert pipeline.is_pipeline_configured() is False
    pipeline.register_stt_factory(lambda: MagicMock())
    assert pipeline.is_pipeline_configured() is False
    assert "TTS" in (pipeline.pipeline_unavailable_reason() or "")
    pipeline.register_tts_factory(lambda: MagicMock())
    assert pipeline.is_pipeline_configured() is True
    assert pipeline.pipeline_unavailable_reason() is None


def test_building_a_session_without_providers_raises_a_typed_error() -> None:
    with pytest.raises(pipeline.VoicePipelineUnavailableError):
        pipeline.build_session()


# ---------------------------------------------------------------------------
# The LiveKit Inference default providers (startup registration)
# ---------------------------------------------------------------------------


@pytest.fixture
def speech_models_configured(monkeypatch: pytest.MonkeyPatch, livekit_configured: None):
    for name, value in (
        ("VOICE_STT_MODEL", "vendor/stt"),
        ("VOICE_STT_LANGUAGE", "multi"),
        ("VOICE_TTS_MODEL", "vendor/tts"),
        ("VOICE_TTS_VOICE_ID", "voice-id"),
        ("VOICE_TTS_LANGUAGE", "hi"),
    ):
        monkeypatch.setattr(settings, name, value, raising=False)


def test_inference_providers_register_when_fully_configured(
    speech_models_configured: None,
) -> None:
    assert pipeline.register_inference_providers() is None
    assert pipeline.is_pipeline_configured() is True


def test_registered_factories_are_lazy_not_eager(speech_models_configured: None) -> None:
    """An STT/TTS client owns a connection and belongs to ONE call, so nothing
    may be constructed at startup -- only at `build_agent` time."""
    pipeline.register_inference_providers()
    assert callable(pipeline._stt_factory)
    assert callable(pipeline._tts_factory)


@pytest.mark.parametrize(
    "missing",
    [
        "VOICE_STT_MODEL",
        "VOICE_STT_LANGUAGE",
        "VOICE_TTS_MODEL",
        "VOICE_TTS_VOICE_ID",
        "VOICE_TTS_LANGUAGE",
    ],
)
def test_one_missing_setting_registers_neither_provider(
    monkeypatch: pytest.MonkeyPatch, speech_models_configured: None, missing: str
) -> None:
    """Half a pipeline is a call where the customer is heard and never answered,
    which is strictly worse than a call with no AI on it at all."""
    monkeypatch.setattr(settings, missing, "", raising=False)

    reason = pipeline.register_inference_providers()

    assert missing in (reason or "")
    assert pipeline.is_pipeline_configured() is False
    assert pipeline._stt_factory is None
    assert pipeline._tts_factory is None


def test_the_kill_switch_beats_a_full_model_configuration(
    monkeypatch: pytest.MonkeyPatch, speech_models_configured: None
) -> None:
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", False, raising=False)
    assert "VOICE_AGENT_ENABLED" in (pipeline.register_inference_providers() or "")
    assert pipeline.is_pipeline_configured() is False


def test_no_livekit_credentials_registers_nothing(
    monkeypatch: pytest.MonkeyPatch, speech_models_configured: None
) -> None:
    """Inference is authenticated with the room credentials; without them the
    models are unreachable, so registering them would only fail later."""
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr(""), raising=False)
    assert "LiveKit is not configured" in (pipeline.register_inference_providers() or "")
    assert pipeline.is_pipeline_configured() is False


@pytest.mark.parametrize(
    ("chat_ctx", "expected"),
    [
        (MagicMock(items=[]), ""),
        (MagicMock(items=None), ""),
    ],
)
def test_latest_user_text_is_defensive_about_sdk_shape(chat_ctx, expected: str) -> None:
    """A mid-call AttributeError in the SDK adapter would be dead air."""
    assert pipeline._latest_user_text(chat_ctx) == expected


def test_latest_user_text_picks_the_most_recent_user_turn() -> None:
    older = MagicMock(role="user", content="first")
    assistant = MagicMock(role="assistant", content="reply")
    newest = MagicMock(role="user", content=["second"])
    ctx = MagicMock(items=[older, assistant, newest])
    assert pipeline._latest_user_text(ctx) == "second"


# ---------------------------------------------------------------------------
# Disabled-mode fallback
# ---------------------------------------------------------------------------


async def test_media_stream_is_refused_when_livekit_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    session = AsyncMock()
    websocket = AsyncMock()

    result = await VoiceService(session).handle_media_stream(websocket, "req-1")

    assert result.started is False
    assert "LiveKit is not configured" in (result.reason or "")
    # Nothing was read, nothing was written: the call is untouched.
    websocket.receive.assert_not_awaited()
    session.execute.assert_not_awaited()


async def test_media_stream_is_refused_when_the_kill_switch_is_off(
    monkeypatch: pytest.MonkeyPatch, livekit_configured
) -> None:
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", False, raising=False)
    _providers_registered()

    result = await VoiceService(AsyncMock()).handle_media_stream(AsyncMock(), "req-1")
    assert result.started is False
    assert result.reason == "VOICE_AGENT_ENABLED is false."


async def test_media_stream_is_refused_without_an_stt_tts_provider(
    livekit_configured,
) -> None:
    """LiveKit alone is not enough -- with no STT/TTS there is no conversation."""
    result = await VoiceService(AsyncMock()).handle_media_stream(AsyncMock(), "req-1")
    assert result.started is False
    assert "provider registered" in (result.reason or "")


async def test_unknown_provider_call_id_is_never_bridged(
    monkeypatch: pytest.MonkeyPatch, livekit_configured
) -> None:
    """A media stream for a call we did not place must be dropped, not joined."""
    _providers_registered()
    monkeypatch.setattr(
        "app.voice.service.resolve_call_by_provider_id", AsyncMock(return_value=None)
    )
    ensure_room = AsyncMock()
    monkeypatch.setattr("app.voice.service.livekit_gateway.ensure_room", ensure_room)

    result = await VoiceService(AsyncMock()).handle_media_stream(AsyncMock(), "not-ours")

    assert result.started is False
    assert result.reason == "UNKNOWN_CALL"
    ensure_room.assert_not_awaited()


async def test_a_failed_room_creation_stops_before_any_audio(
    monkeypatch: pytest.MonkeyPatch, livekit_configured
) -> None:
    _providers_registered()
    context = _context()
    monkeypatch.setattr(
        "app.voice.service.VoiceService.prepare", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        "app.voice.service.livekit_gateway.ensure_room", AsyncMock(return_value=False)
    )
    websocket = AsyncMock()

    result = await VoiceService(AsyncMock()).handle_media_stream(websocket, "req-1")

    assert result.started is False
    assert result.reason == "ROOM_CREATE_FAILED"
    websocket.receive.assert_not_awaited()


async def test_preflight_reports_the_first_blocking_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    assert VoiceService.preflight() is not None


async def test_preflight_is_clear_once_everything_is_wired(livekit_configured) -> None:
    _providers_registered()
    assert VoiceService.preflight() is None


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invariant",
    [
        "PHONE CALL",
        "NEVER RE-ASK",
        "Never invent",
        "ONLY READ",
        "HUMAN",
    ],
)
def test_system_prompt_states_its_non_negotiables(invariant: str) -> None:
    assert invariant.lower() in SYSTEM_PROMPT.lower()


def test_system_prompt_constrains_spoken_length() -> None:
    """The prompt asks for brevity and `_say` enforces it; both must exist."""
    assert "40 words" in SYSTEM_PROMPT
    assert MAX_SPOKEN_REPLY_CHARS < 500


def test_briefing_carries_every_spec_section_4_field() -> None:
    briefing = build_briefing(_context())
    for expected in (
        "Asha Rao",
        "3 BHK in Whitefield",
        "80L to 1Cr",
        "Asked about Palm Grove on WhatsApp.",
        "The customer asked to be called.",
    ):
        assert expected in briefing


def test_briefing_says_not_known_rather_than_omitting_a_field() -> None:
    """The model must be able to tell 'we have no budget' from 'truncated'."""
    briefing = build_briefing(
        VoiceCallContext(
            tenant_id=uuid4(),
            contact_id=uuid4(),
            call_id=uuid4(),
            call_job_id=uuid4(),
            call_attempt_id=uuid4(),
        )
    )
    assert "(not known)" in briefing
    assert "no previous conversation on file" in briefing


def test_turn_prompt_puts_the_newest_utterance_last() -> None:
    prompt = build_turn_prompt(
        _context(),
        [{"speaker": "agent", "text": "Hello!"}],
        "What is the price?",
    )
    assert prompt.index("Hello!") < prompt.index("What is the price?")
    assert prompt.rstrip().endswith("What is the price?")


def test_turn_prompt_marks_the_very_first_turn_explicitly() -> None:
    assert "opening of the call" in build_turn_prompt(_context(), [], "Hi?")


def test_opening_prompt_asks_for_exactly_one_question() -> None:
    prompt = build_opening_prompt(_context())
    assert "just picked up" in prompt
    assert "one opening" in prompt


# ---------------------------------------------------------------------------
# Outcome schema
# ---------------------------------------------------------------------------


def test_outcome_schema_is_legal_for_structured_outputs() -> None:
    """Structured outputs reject a loose schema: no additionalProperties, and
    every property must be required."""
    assert OUTCOME_SCHEMA["additionalProperties"] is False
    assert set(OUTCOME_SCHEMA["required"]) == set(OUTCOME_SCHEMA["properties"])


def test_outcome_values_are_a_subset_of_the_db_enum() -> None:
    """Anything outside `public.call_attempt_outcome` would be a 22P02 on insert."""
    from app.agent.gateway import RETRYABLE_OUTCOMES, TERMINAL_OUTCOMES

    assert set(OUTCOME_VALUES) <= (RETRYABLE_OUTCOMES | TERMINAL_OUTCOMES)


def test_the_model_cannot_claim_a_telephony_outcome_it_cannot_observe() -> None:
    """'no_answer'/'busy'/'wrong_number' are facts Superfone reports, not
    conclusions a connected conversation may draw."""
    for telephony_only in ("no_answer", "busy", "rejected", "wrong_number"):
        assert telephony_only not in OUTCOME_VALUES


def test_technical_failure_is_never_offered_to_the_model() -> None:
    """It is set by the failure path in code, so the model cannot fake one."""
    assert "technical_failure" not in OUTCOME_VALUES
