"""Unit tests for configuration loading and environment validation."""

from pydantic import SecretStr

from app.core.config import Settings


def test_settings_default_values() -> None:
    """Verify settings properties and environment flags."""
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=SecretStr("postgresql+asyncpg://usr:pwd@localhost:5432/db"),
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY=SecretStr("secret-key"),
        SUPABASE_JWT_SECRET=SecretStr("jwt-secret"),
        REDIS_URL=SecretStr("redis://localhost:6379/0"),
    )

    assert settings.APP_NAME == "Real Estate CRM Backend"
    assert settings.is_test is True
    assert settings.is_production is False
    assert settings.is_development is False
    assert (
        settings.DATABASE_URL.get_secret_value() == "postgresql+asyncpg://usr:pwd@localhost:5432/db"
    )


def test_cors_origins_parsing() -> None:
    """Verify CORS origins string parsing."""
    settings = Settings(
        DATABASE_URL=SecretStr("postgresql+asyncpg://usr:pwd@localhost:5432/db"),
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY=SecretStr("secret-key"),
        SUPABASE_JWT_SECRET=SecretStr("jwt-secret"),
        REDIS_URL=SecretStr("redis://localhost:6379/0"),
        CORS_ALLOWED_ORIGINS=["http://localhost:3000", "http://127.0.0.1:3000"],
    )

    assert settings.CORS_ALLOWED_ORIGINS == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
