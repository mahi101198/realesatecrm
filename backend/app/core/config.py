"""Application Configuration powered by Pydantic Settings."""

from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors_origins(v: str | list[str]) -> list[str]:
    """Parse comma-separated CORS origins string into a list."""
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return v


CorsOrigins = Annotated[list[str], BeforeValidator(parse_cors_origins)]


class Settings(BaseSettings):
    """Application settings loaded securely from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Core Application Settings
    APP_NAME: str = Field(default="Real Estate CRM Backend", description="Application Title")
    APP_ENV: str = Field(
        default="development",
        description="Environment: development, test, staging, production",
    )
    DEBUG: bool = Field(default=False, description="Enable debug mode")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    API_V1_PREFIX: str = Field(default="/api/v1", description="API V1 Base Prefix")

    # Database Configuration (PostgreSQL / Supabase)
    DATABASE_URL: SecretStr = Field(
        ...,
        description="Async PostgreSQL Connection String (postgresql+asyncpg://...)",
    )
    DB_POOL_SIZE: int = Field(default=10, description="SQLAlchemy connection pool size")
    DB_MAX_OVERFLOW: int = Field(default=20, description="SQLAlchemy pool max overflow")
    DB_POOL_TIMEOUT: int = Field(default=30, description="SQLAlchemy pool timeout in seconds")

    # Supabase Integration & Auth Infrastructure
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_SERVICE_ROLE_KEY: SecretStr = Field(
        ...,
        description="Supabase service role secret key",
    )
    SUPABASE_JWT_SECRET: SecretStr = Field(
        ...,
        description="Supabase JWT verification secret key",
    )

    # Redis Ephemeral State
    REDIS_URL: SecretStr = Field(..., description="Redis connection DSN (redis://...)")

    # Security & CORS
    CORS_ALLOWED_ORIGINS: CorsOrigins = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS Origins",
    )

    # Rate Limiting Foundation
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, description="Default rate limit per minute")
    PUBLIC_INTAKE_RATE_LIMIT_PER_MINUTE: int = Field(
        default=5,
        description=(
            "Per-IP-per-tenant rate limit for the unauthenticated public lead "
            "intake endpoint. Deliberately much stricter than the general API "
            "rate limit since this endpoint has no auth/permission gate at all."
        ),
    )
    # Public base URL this app is reachable at, used to build fully-qualified
    # webhook callback URLs (e.g. Superfone's answer_url/ring_url/hangup_url)
    # that an external provider calls back into. Not part of the original
    # settings surface; added because outbound-call webhook registration is
    # impossible without it.
    APP_PUBLIC_BASE_URL: str = Field(
        default="http://localhost:8000",
        description=(
            "Publicly reachable base URL for this app, used to build webhook callback URLs."
        ),
    )

    # ---- Superfone Telephony Integration ------------------------------
    # Two separate API surfaces, two separate credential pairs -- see
    # app/integrations/superfone/client.py for why they are never merged.
    SUPERFONE_SFVOPI_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="X-API-Key for Superfone's SFVoPI (AI voice) API.",
    )
    SUPERFONE_SFVOPI_BASE_URL: str = Field(
        default="https://prod-api.superfone.co.in/superfone/sfvopi/",
        description="Base URL for Superfone's SFVoPI API.",
    )
    SUPERFONE_SFVOPI_FROM_NUMBER: str | None = Field(
        default=None,
        description=(
            "E.164 outbound caller ID linked to an active SFVoPI app. Platform-wide "
            "for now (single shared Superfone account) -- not part of the original "
            "settings list, added because initiate_outbound_call requires a `from` "
            "number and nothing else in this schema stores one yet."
        ),
    )
    SUPERFONE_CRM_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="x-api-key for Superfone's CRM API (click-to-call).",
    )
    SUPERFONE_CRM_BASE_URL: str = Field(
        default="https://prod-api.superfone.co.in/superfone/api/",
        description="Base URL for Superfone's CRM API.",
    )
    SUPERFONE_WEBHOOK_SHARED_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Shared secret embedded as a query token in the answer_url/ring_url/"
            "hangup_url we register with Superfone's SFVoPI initiate-call request. "
            "SFVoPI webhooks support no HMAC signature and no dashboard-configurable "
            "auth header, so this URL-embedded token is our own defense-in-depth."
        ),
    )
    VOICE_AGENT_STREAM_URL: str = Field(
        default="",
        description=(
            "wss:// endpoint the SFVoPI answer webhook advertises in its Stream JSON, "
            "i.e. where Superfone opens the bidirectional media WebSocket. Historically "
            "this pointed at an external service; `app/voice/router.py` now implements "
            "a compatible endpoint inside this app "
            "(wss://<host>{API_V1_PREFIX}/voice/stream), so this should normally be set "
            "to that URL. Left configurable so an external bridge can still be used."
        ),
    )
    VOICE_AGENT_STREAM_CODEC: str = Field(
        default="PCMA",
        description=(
            "Codec for the SFVoPI media stream. PCMA@8000Hz matches Superfone's "
            "native telephony stack."
        ),
    )
    VOICE_AGENT_STREAM_SAMPLE_RATE: int = Field(
        default=8000,
        description="Sample rate (Hz) for the SFVoPI media stream.",
    )

    # ---- LiveKit (voice-agent media plane) -----------------------------
    # LiveKit does NOT replace Superfone: Superfone still dials the PSTN leg
    # and still streams that leg's audio to VOICE_AGENT_STREAM_URL. LiveKit is
    # the room this app bridges that audio INTO so an AI agent can participate.
    # Optional by design, exactly like ANTHROPIC_API_KEY: with any of these
    # three empty, `app/voice/livekit_gateway.py::is_livekit_configured` is
    # False, the whole voice layer no-ops, and the call still gets placed and
    # tracked by the existing Superfone flow -- it just has no AI in the room.
    LIVEKIT_URL: str = Field(
        default="",
        description=(
            "LiveKit server URL (wss://<project>.livekit.cloud). Empty disables the "
            "voice-agent media plane entirely."
        ),
    )
    LIVEKIT_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="LiveKit API key used to mint room access tokens and manage rooms.",
    )
    LIVEKIT_API_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description="LiveKit API secret paired with LIVEKIT_API_KEY. Never logged.",
    )
    VOICE_AGENT_ENABLED: bool = Field(
        default=True,
        description=(
            "Master kill-switch for the AI voice-agent layer, mirroring "
            "AI_ORCHESTRATOR_ENABLED. When False (or when LiveKit/Anthropic "
            "credentials are missing) Superfone media streams are accepted and "
            "closed cleanly instead of being bridged into a LiveKit room."
        ),
    )

    # ---- LiveKit Inference Gateway (voice reasoning + speech models) ----
    # The voice agent does NOT reason through Anthropic. It reasons through
    # LiveKit's Inference Gateway, authenticated with the LIVEKIT_API_KEY /
    # LIVEKIT_API_SECRET pair above (no extra credential), so the whole voice
    # turn -- speech in, reasoning, speech out -- lives behind one vendor and
    # one round trip to one edge. WhatsApp keeps using Anthropic
    # (`app/agents/llm.py`); these two backends are parallel, not layered.
    #
    # Every default is "" on purpose: an unset model id means the voice layer
    # simply never registers that provider and degrades to "the Superfone call
    # happens, it just carries no AI" -- identical to the posture every other
    # optional provider in this file takes.
    VOICE_LLM_MODEL: str = Field(
        default="",
        description=(
            "LiveKit Inference model id used for the voice agent's reasoning "
            "turns (e.g. 'google/gemma-4-31b-it'). Empty disables the voice "
            "reasoning plane, which disables the voice agent entirely."
        ),
    )
    VOICE_STT_MODEL: str = Field(
        default="",
        description=(
            "LiveKit Inference speech-to-text model id (e.g. 'deepgram/nova-3'). "
            "Empty means no STT provider is registered and the voice pipeline "
            "stays unconfigured."
        ),
    )
    VOICE_STT_LANGUAGE: str = Field(
        default="",
        description=(
            "Language hint for the STT model. 'multi' lets one model handle the "
            "Hindi/English code-switching that is normal on Indian telephony."
        ),
    )
    VOICE_TTS_MODEL: str = Field(
        default="",
        description=(
            "LiveKit Inference text-to-speech model id (e.g. 'cartesia/sonic-3'). "
            "Empty means no TTS provider is registered."
        ),
    )
    VOICE_TTS_VOICE_ID: str = Field(
        default="",
        description=(
            "Provider-specific voice id the TTS model speaks with. Required "
            "alongside VOICE_TTS_MODEL; empty leaves TTS unregistered."
        ),
    )
    VOICE_TTS_LANGUAGE: str = Field(
        default="",
        description=(
            "Language the TTS voice speaks (e.g. 'hi'). Kept separate from "
            "VOICE_STT_LANGUAGE: we listen in many languages but answer in one."
        ),
    )

    WHATSAPP_CREDENTIALS_ENCRYPTION_KEY: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Base64-encoded 32-byte Fernet key used to encrypt each tenant's "
            "Meta WhatsApp access_token and app_secret at rest in "
            "whatsapp_tenant_configs. Generate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`. Required before any "
            "tenant WhatsApp credentials can be stored or used -- "
            "app/integrations/whatsapp/crypto.py fails closed if empty."
        ),
    )
    WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Expected value of the Authorization: Bearer header on the "
            "whatsapp_busness_dashboard product's call-agent trigger "
            "requests (POST /webhooks/whatsapp-dashboard/call-agent). That "
            "product is a separate, unmodified repository -- this secret is "
            "configured as its CALL_AGENT_API_KEY environment variable."
        ),
    )

    # ---- Anthropic (AI orchestration + WhatsApp conversational layer) ----
    # Optional by design: the whole AI layer fails CLOSED when the key is
    # empty. `app/agents/llm.py::is_llm_configured` gates every entry point,
    # so a deployment without a key keeps the deterministic CRM/webhook
    # pipeline working exactly as before instead of erroring per message.
    ANTHROPIC_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "API key for the Anthropic Messages API, used by the lead workflow "
            "orchestrator (intent classification) and the WhatsApp agent (reply "
            "composition). Empty disables the AI layer entirely."
        ),
    )
    ANTHROPIC_MODEL: str = Field(
        default="claude-opus-5",
        description=(
            "Anthropic model id used for every AI call in this app. Configurable "
            "on purpose -- swap to a cheaper tier (e.g. claude-haiku-4-5) for "
            "high-volume WhatsApp traffic without touching code."
        ),
    )
    ANTHROPIC_MAX_TOKENS: int = Field(
        default=2048,
        description=(
            "Default max_tokens for AI calls. Sized for short WhatsApp replies and "
            "small structured-output payloads; note this caps thinking + response "
            "text together."
        ),
    )
    AI_ORCHESTRATOR_ENABLED: bool = Field(
        default=True,
        description=(
            "Master kill-switch for the inbound-WhatsApp AI orchestration layer. "
            "When False (or when ANTHROPIC_API_KEY is empty) inbound messages are "
            "still stored and instrumented, but no AI reply/action is produced."
        ),
    )

    TRUST_PROXY_HEADERS: bool = Field(
        default=False,
        description=(
            "Whether to trust the X-Forwarded-For header for client IP "
            "resolution (used by the public intake rate limiter). Fail-safe "
            "default is False: always use the direct connection address and "
            "ignore X-Forwarded-For entirely, since it is fully attacker-"
            "controlled input on a direct connection. Only set this to True "
            "when this deployment's proxy/load-balancer layer is known to "
            "strip or overwrite any inbound client-supplied X-Forwarded-For "
            "before forwarding the request -- otherwise any caller can spoof "
            "a new IP per request and defeat the rate limiter entirely on the "
            "one endpoint where it is the actual abuse defense."
        ),
    )

    @property
    def is_production(self) -> bool:
        """Return True if running in production environment."""
        return self.APP_ENV.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Return True if running in development environment."""
        return self.APP_ENV.lower() == "development"

    @property
    def is_test(self) -> bool:
        """Return True if running in test environment."""
        return self.APP_ENV.lower() == "test"


settings = Settings()  # type: ignore[call-arg]
