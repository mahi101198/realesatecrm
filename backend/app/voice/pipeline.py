"""The livekit-agents pipeline seam: STT -> VoiceAgent -> TTS.

WHAT IS REAL HERE AND WHAT IS NOT
    REAL: the wiring. `CrmVoiceAgent` is a `livekit.agents.Agent` whose
    `llm_node` is overridden to delegate to our `VoiceAgent.respond()`, so the
    reasoning, the tool allowlist, the CRM briefing and the transcript all come
    from `app/voice/agent.py` and NOT from a second LLM client. That override is
    the whole point: livekit-agents' own LLM/tool machinery is bypassed, which
    is what keeps `app/agents/llm.py` the only Anthropic seam in this codebase.

    THE STT/TTS SEAM IS STILL INJECTION, IT JUST HAS A DEFAULT FILLING NOW.
    `register_stt_factory` / `register_tts_factory` remain the only way a
    provider gets in, because the choice is a deployment decision (language
    coverage for Hindi/English telephony audio, cost, latency, data residency)
    and hard-wiring one would force it on every tenant. What changed is that
    `register_inference_providers()` below offers a config-driven default:
    LiveKit's own Inference Gateway, selected purely by the VOICE_STT_* /
    VOICE_TTS_* settings and authenticated with the LiveKit credentials the
    room already uses. With those settings empty NOTHING is registered,
    `is_pipeline_configured()` stays False, and the voice layer degrades
    exactly as it does with no LiveKit credentials at all: the call still
    happens over Superfone, it just carries no AI.

    A site that wants a different vendor installs `livekit-plugins-*` and calls
    the two register_* functions itself instead; nothing here is load-bearing
    for that path.

    STILL UNVERIFIED: the `AgentSession` lifecycle below has not been executed
    against a live room with a real PSTN media stream -- there is no phone call
    in the build environment. The models HAVE been exercised live against the
    gateway (LLM chat, TTS synth), including through the exact code path
    `service.py::VoiceService._run` uses, which surfaced and fixed a real bug:
    `inference.STT`/`TTS` need an aiohttp session from
    `livekit.agents.utils.http_context`, normally opened by the
    `agents.cli.run_app` worker process this codebase does not run. `_run` now
    wraps itself in `http_context.open()` to supply one manually -- without it,
    every real call would have failed on its first STT/TTS use with
    `APIConnectionError: Attempted to use an http session outside of a job
    context`. The room-join and PCMA<->PCM16 bridging path is still unverified
    end-to-end.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.voice.context import VoiceCallContext
from app.voice.livekit_gateway import VoiceLayerError, is_livekit_configured
from app.voice.prompts import SYSTEM_PROMPT, build_briefing

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from livekit import agents as lk_agents

logger = logging.getLogger(__name__)

STTFactory = Callable[[], Any]
TTSFactory = Callable[[], Any]

_stt_factory: STTFactory | None = None
_tts_factory: TTSFactory | None = None


class VoicePipelineUnavailableError(VoiceLayerError):
    """No STT/TTS provider has been registered, so no audio pipeline exists."""


def register_stt_factory(factory: STTFactory | None) -> None:
    """Install (or clear, with None) the speech-to-text provider factory."""
    global _stt_factory
    _stt_factory = factory


def register_tts_factory(factory: TTSFactory | None) -> None:
    """Install (or clear, with None) the text-to-speech provider factory."""
    global _tts_factory
    _tts_factory = factory


def inference_providers_unavailable_reason() -> str | None:
    """Why the LiveKit-Inference default providers cannot be registered, or None.

    Every clause is a plain "this setting is empty", never an error: running
    without speech models is a supported configuration, so this is a log line
    at startup, not an exception.
    """
    if not settings.VOICE_AGENT_ENABLED:
        return "VOICE_AGENT_ENABLED is false."
    if not is_livekit_configured():
        return "LiveKit is not configured (LIVEKIT_URL/API_KEY/API_SECRET)."
    missing = [
        name
        for name, value in (
            ("VOICE_STT_MODEL", settings.VOICE_STT_MODEL),
            ("VOICE_STT_LANGUAGE", settings.VOICE_STT_LANGUAGE),
            ("VOICE_TTS_MODEL", settings.VOICE_TTS_MODEL),
            ("VOICE_TTS_VOICE_ID", settings.VOICE_TTS_VOICE_ID),
            ("VOICE_TTS_LANGUAGE", settings.VOICE_TTS_LANGUAGE),
        )
        if not value.strip()
    ]
    if missing:
        return f"Speech model settings are not configured: {', '.join(missing)}."
    return None


def register_inference_providers() -> str | None:
    """Register LiveKit Inference as the STT/TTS pair, if it is fully configured.

    Returns None when both providers were registered, or the human-readable
    reason nothing was. ALL-OR-NOTHING on purpose: half a pipeline (ears but no
    voice) is a call where the customer is listened to and never answered,
    which is strictly worse than a call with no AI on it at all. So a single
    missing setting leaves BOTH factories `None` and
    `is_pipeline_configured()` False, which is the state the whole voice layer
    already knows how to degrade from.

    The factories are lazy closures, not instances: an STT/TTS client owns a
    connection and belongs to one call, so it is constructed per call inside
    `build_agent`, while this function runs once at startup.
    """
    reason = inference_providers_unavailable_reason()
    if reason is not None:
        logger.info(f"LiveKit Inference speech providers not registered: {reason}")
        return reason

    try:
        from livekit.agents import inference as lk_inference
    except ImportError as exc:  # pragma: no cover -- the SDK is a hard dependency
        logger.error(f"livekit-agents inference models are unavailable: {exc!s}")
        return "livekit-agents is not installed."

    def _stt() -> Any:
        return lk_inference.STT(
            model=settings.VOICE_STT_MODEL,
            language=settings.VOICE_STT_LANGUAGE,
            api_key=settings.LIVEKIT_API_KEY.get_secret_value(),
            api_secret=settings.LIVEKIT_API_SECRET.get_secret_value(),
        )

    def _tts() -> Any:
        return lk_inference.TTS(
            model=settings.VOICE_TTS_MODEL,
            voice=settings.VOICE_TTS_VOICE_ID,
            language=settings.VOICE_TTS_LANGUAGE,
            api_key=settings.LIVEKIT_API_KEY.get_secret_value(),
            api_secret=settings.LIVEKIT_API_SECRET.get_secret_value(),
        )

    register_stt_factory(_stt)
    register_tts_factory(_tts)
    logger.info(
        f"Voice pipeline using LiveKit Inference: stt={settings.VOICE_STT_MODEL} "
        f"tts={settings.VOICE_TTS_MODEL}"
    )
    return None


def is_pipeline_configured() -> bool:
    """True once both an STT and a TTS provider have been registered."""
    return _stt_factory is not None and _tts_factory is not None


def pipeline_unavailable_reason() -> str | None:
    """Why there is no audio pipeline, or None when there is one."""
    missing = [
        name
        for name, factory in (("STT", _stt_factory), ("TTS", _tts_factory))
        if factory is None
    ]
    if not missing:
        return None
    return (
        f"No {' and no '.join(missing)} provider registered; "
        "see app/voice/pipeline.py for how to register one."
    )


def build_agent(context: VoiceCallContext, conversation: Any) -> "lk_agents.Agent":
    """Build the livekit-agents `Agent` for one call.

    `conversation` is our `app.voice.agent.VoiceAgent`. It is typed `Any` to
    keep this module importable (and testable) without dragging in the CRM
    service graph -- the only thing this module requires of it is an awaitable
    `respond(str) -> str` and an awaitable `open() -> str`.
    """
    from livekit import agents as lk_agents

    class CrmVoiceAgent(lk_agents.Agent):
        """A livekit Agent whose 'LLM' is our CRM-aware VoiceAgent."""

        async def llm_node(
            self,
            chat_ctx: Any,
            tools: Any,  # noqa: ARG002 -- our tool surface is owned by VoiceAgent
            model_settings: Any,  # noqa: ARG002 -- ditto; signature is the SDK's
        ) -> str:
            """Turn the newest user message into a spoken reply.

            Returning a plain string is one of the shapes the SDK accepts (see
            `Agent.llm_node`'s return annotation); it means "no streaming", and
            no streaming is correct here because `VoiceAgent.respond` may run a
            multi-round tool loop before it knows its first word.
            """
            utterance = _latest_user_text(chat_ctx)
            if not utterance:
                return ""
            return str(await conversation.respond(utterance))

    return CrmVoiceAgent(
        instructions=f"{SYSTEM_PROMPT}\n\n{build_briefing(context)}",
        stt=_require(_stt_factory, "STT")(),
        tts=_require(_tts_factory, "TTS")(),
        # No `llm=` and no `tools=`: `llm_node` above short-circuits the SDK's
        # own model call, and the read-only tool surface lives in VoiceAgent.
    )


def build_session() -> "lk_agents.AgentSession[None]":
    """Build the `AgentSession` that drives the agent inside a room.

    `llm=_NeverCalledLLM()` IS LOAD-BEARING, not decorative -- see that class's
    docstring. Discovered live (Aug 2026): `AgentActivity._generate_reply`
    (livekit-agents internal) raises `RuntimeError("trying to generate reply
    without an LLM model")` whenever `self.llm is None`, checked BEFORE
    `llm_node` is ever reached, for every reply the SDK generates itself after
    STT/VAD finalizes a customer utterance -- not just the ones this app drives
    via `.say()`/`.generate_reply()` with pre-computed text. With no `llm=`
    anywhere (the state this code shipped in originally), the opening line
    worked (`.say()` never goes through `_generate_reply`) but EVERY real
    conversational turn raised inside the SDK's own background task -- silent
    to this app's logs, which is why a live call would speak its opener and
    then never respond again no matter what the customer said.
    """
    if not is_pipeline_configured():
        raise VoicePipelineUnavailableError(
            pipeline_unavailable_reason() or "Voice pipeline is not configured."
        )
    from livekit import agents as lk_agents
    from livekit.agents import llm as lk_llm

    class _NeverCalledLLM(lk_llm.LLM):
        """Satisfies `self.llm is not None` without ever actually running.

        `CrmVoiceAgent.llm_node` intercepts every reply before the SDK would
        reach an actual `.chat()` call -- see that method's docstring. This
        object exists ONLY to be non-None; `chat()` raises if the SDK ever
        calls it directly, which would mean `llm_node` silently stopped being
        invoked. Failing loudly here beats a stub that would return
        empty/garbage output. Defined locally (not at module scope) for the
        same lazy-SDK-import reason as `CrmVoiceAgent` above.
        """

        def chat(self, *_args: Any, **_kwargs: Any) -> Any:
            raise VoicePipelineUnavailableError(
                "The stub LLM was invoked directly -- llm_node should have "
                "intercepted this call before it reached the SDK's default "
                "LLM pipeline."
            )

    return lk_agents.AgentSession(llm=_NeverCalledLLM())


def _require(factory: STTFactory | TTSFactory | None, kind: str) -> STTFactory | TTSFactory:
    if factory is None:
        raise VoicePipelineUnavailableError(f"No {kind} provider registered.")
    return factory


def _latest_user_text(chat_ctx: Any) -> str:
    """Extract the most recent user utterance from a livekit ChatContext.

    Defensive rather than duck-typed-optimistic: the SDK's chat item shape has
    changed across majors, and a mid-call AttributeError here would be dead air.
    """
    items = getattr(chat_ctx, "items", None) or []
    for item in reversed(list(items)):
        if getattr(item, "role", None) != "user":
            continue
        content = getattr(item, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [c for c in content if isinstance(c, str)]
            if parts:
                return " ".join(parts).strip()
        text_content = getattr(item, "text_content", None)
        if isinstance(text_content, str):
            return text_content.strip()
    return ""
