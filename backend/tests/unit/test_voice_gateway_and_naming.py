"""Voice layer: configuration gating, room naming, and the disabled fallback.

These are the parts that MUST work without LiveKit, without Anthropic and
without a phone call, because they are what decides whether anything else in
the package runs at all.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings, settings
from app.voice import livekit_gateway
from app.voice.livekit_gateway import (
    LiveKitUnavailableError,
    is_livekit_configured,
    is_voice_agent_enabled,
    parse_room_name,
    room_name_for_call,
    voice_disabled_reason,
)


def _settings(**overrides):
    # `_env_file=None` is load-bearing: these tests assert on the DEFAULTS a
    # deployment gets with nothing configured, and without it pydantic-settings
    # reads the developer's real `.env` and the assertions become a statement
    # about whoever ran them.
    return Settings(
        _env_file=None,
        DATABASE_URL=SecretStr("postgresql+asyncpg://usr:pwd@localhost:5432/db"),
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY=SecretStr("secret-key"),
        SUPABASE_JWT_SECRET=SecretStr("jwt-secret"),
        REDIS_URL=SecretStr("redis://localhost:6379/0"),
        **overrides,
    )


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------


def test_livekit_settings_default_to_disabled() -> None:
    """An untouched deployment must have the voice media plane switched off."""
    cfg = _settings()
    assert cfg.LIVEKIT_URL == ""
    assert cfg.LIVEKIT_API_KEY.get_secret_value() == ""
    assert cfg.LIVEKIT_API_SECRET.get_secret_value() == ""
    # The kill-switch defaults on, mirroring AI_ORCHESTRATOR_ENABLED: absent
    # credentials are what disables the layer, not the flag.
    assert cfg.VOICE_AGENT_ENABLED is True


def test_voice_model_settings_default_to_unset() -> None:
    """No model id means no provider is registered, which is the OFF state."""
    cfg = _settings()
    assert cfg.VOICE_LLM_MODEL == ""
    assert cfg.VOICE_STT_MODEL == ""
    assert cfg.VOICE_STT_LANGUAGE == ""
    assert cfg.VOICE_TTS_MODEL == ""
    assert cfg.VOICE_TTS_VOICE_ID == ""
    assert cfg.VOICE_TTS_LANGUAGE == ""


def test_livekit_secrets_are_secretstr() -> None:
    """Credentials must never be plain strings that can leak into a log line."""
    cfg = _settings(
        LIVEKIT_API_KEY=SecretStr("APIabc"), LIVEKIT_API_SECRET=SecretStr("shh")
    )
    assert "shh" not in repr(cfg.LIVEKIT_API_SECRET)
    assert "APIabc" not in str(cfg.LIVEKIT_API_KEY)


# ---------------------------------------------------------------------------
# Gating / graceful degradation
# ---------------------------------------------------------------------------


def test_not_configured_when_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    assert is_livekit_configured() is False
    assert is_voice_agent_enabled() is False
    assert "LiveKit is not configured" in (voice_disabled_reason() or "")


@pytest.mark.parametrize(
    ("url", "key", "secret"),
    [
        ("", "k", "s"),
        ("wss://x.livekit.cloud", "", "s"),
        ("wss://x.livekit.cloud", "k", ""),
        ("   ", "k", "s"),
    ],
)
def test_partial_credentials_never_count_as_configured(
    monkeypatch: pytest.MonkeyPatch, url: str, key: str, secret: str
) -> None:
    """All three or nothing -- two out of three is a misconfiguration, not a
    half-working voice layer."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", url, raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr(key), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr(secret), raising=False)
    assert is_livekit_configured() is False


def test_kill_switch_beats_present_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://x.livekit.cloud", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "vendor/model", raising=False)
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", False, raising=False)

    assert is_livekit_configured() is True
    assert is_voice_agent_enabled() is False
    assert voice_disabled_reason() == "VOICE_AGENT_ENABLED is false."


def test_livekit_without_a_voice_model_is_still_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A room with no LLM behind it is a silent phone call. Refuse to open one.

    Note the reasoning plane is VOICE_LLM_MODEL on LiveKit Inference, NOT
    Anthropic: a voice-only deployment needs no Anthropic key at all.
    """
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://x.livekit.cloud", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "", raising=False)

    assert is_voice_agent_enabled() is False
    assert "VOICE_LLM_MODEL" in (voice_disabled_reason() or "")


def test_an_absent_anthropic_key_does_not_disable_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice moved off the shared Anthropic seam; the gate must have moved too."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://x.livekit.cloud", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "vendor/model", raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", SecretStr(""), raising=False)

    assert is_voice_agent_enabled() is True


def test_fully_configured_enables_the_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://x.livekit.cloud", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "vendor/model", raising=False)

    assert is_voice_agent_enabled() is True
    assert voice_disabled_reason() is None


async def test_ensure_room_is_a_noop_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Room creation must return False, not raise, when LiveKit is absent."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    assert await livekit_gateway.ensure_room("call-x") is False
    assert await livekit_gateway.delete_room("call-x") is False


def test_token_minting_refuses_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the no-op paths, minting a token has no safe fallback value."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "", raising=False)
    with pytest.raises(LiveKitUnavailableError):
        livekit_gateway.build_access_token(room_name="call-x", identity="who")
    with pytest.raises(LiveKitUnavailableError):
        livekit_gateway.get_room_client()


# ---------------------------------------------------------------------------
# Room naming
# ---------------------------------------------------------------------------


def test_room_name_round_trips_to_the_ids_it_encodes() -> None:
    job_id, attempt_id = uuid4(), uuid4()
    name = room_name_for_call(job_id, attempt_id)
    assert parse_room_name(name) == (job_id, attempt_id)


def test_room_name_is_deterministic() -> None:
    """The websocket handler and any later teardown derive it independently."""
    job_id, attempt_id = uuid4(), uuid4()
    assert room_name_for_call(job_id, attempt_id) == room_name_for_call(job_id, attempt_id)


def test_room_name_is_unique_per_attempt_not_per_job() -> None:
    """A retried job dials again; its new room must not collide with the old."""
    job_id = uuid4()
    first = room_name_for_call(job_id, uuid4())
    second = room_name_for_call(job_id, uuid4())
    assert first != second


def test_room_name_contains_no_separators_livekit_would_reject() -> None:
    name = room_name_for_call(uuid4(), uuid4())
    assert " " not in name
    assert "/" not in name
    assert name.startswith("call-")


@pytest.mark.parametrize(
    "hostile",
    [
        "",
        "some-other-room",
        "call-notahex-notahex",
        f"call-{uuid4().hex}",
        f"CALL-{uuid4().hex}-{uuid4().hex}",
        f"call-{uuid4().hex}-{uuid4().hex}-extra",
    ],
)
def test_parse_room_name_rejects_rooms_we_did_not_create(hostile: str) -> None:
    """A room we did not name must never be mistaken for one of our calls."""
    assert parse_room_name(hostile) is None


def test_parse_room_name_accepts_the_canonical_form() -> None:
    job = UUID("11111111-1111-1111-1111-111111111111")
    attempt = UUID("22222222-2222-2222-2222-222222222222")
    assert parse_room_name(f"call-{job.hex}-{attempt.hex}") == (job, attempt)
