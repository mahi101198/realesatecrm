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
    SUPERFONE_CRM_WEBHOOK_BEARER_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Expected value of the Authorization: Bearer header on Superfone CRM "
            "event-notification webhooks. Configured manually in Superfone's "
            "dashboard automations UI (their CRM webhooks DO support a "
            "dashboard-configured auth header, unlike SFVoPI's)."
        ),
    )
    VOICE_AGENT_STREAM_URL: str = Field(
        default="",
        description=(
            "External wss:// endpoint for the voice-agent audio stream. This repo "
            "does not run that WebSocket server -- the SFVoPI answer webhook only "
            "returns Stream JSON pointing at it."
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
    SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Shared secret embedded as a query token in the WhatsApp (Dragonfly) "
            "webhook URL we give Superfone to register (there is no self-service "
            "registration API for it, and no signature/auth header exists on this "
            "webhook stream either, per Superfone's own docs) -- same URL-embedded-"
            "token pattern as SUPERFONE_WEBHOOK_SHARED_SECRET, but a separate secret "
            "value so the two webhook streams can be rotated independently."
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
