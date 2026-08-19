"""Unit tests for WhatsAppTenantConfigRepository (encrypt-on-write,
never-decrypt-in-the-public-read-path)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository


@pytest.mark.asyncio
async def test_upsert_encrypts_secrets_before_insert() -> None:
    """Verify the raw SQL params passed to session.execute carry ciphertext,
    never the plaintext access_token/app_secret arguments."""
    mock_session = AsyncMock()
    upsert_res = MagicMock()
    upsert_res.mappings.return_value.one.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "is_active": True,
    }
    mock_session.execute.return_value = upsert_res
    repo = WhatsAppTenantConfigRepository(mock_session)
    tenant_id = uuid4()

    with (
        patch(
            "app.integrations.whatsapp.repository.encrypt_secret",
            side_effect=lambda v: f"encrypted:{v}",
        ) as mock_encrypt,
    ):
        await repo.upsert(
            tenant_id=tenant_id,
            waba_id="waba-1",
            phone_number_id="phone-1",
            verify_token="verify-1",
            access_token_plain="plain-access-token",
            app_secret_plain="plain-app-secret",
        )

    mock_encrypt.assert_any_call("plain-access-token")
    mock_encrypt.assert_any_call("plain-app-secret")
    params = mock_session.execute.call_args.args[1]
    assert params["access_token_encrypted"] == "encrypted:plain-access-token"
    assert params["app_secret_encrypted"] == "encrypted:plain-app-secret"
    assert "plain-access-token" not in params.values()


@pytest.mark.asyncio
async def test_get_public_never_returns_secret_columns() -> None:
    """Verify get_public's SELECT does not fetch the encrypted columns at
    all -- not just that the response omits them."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "is_active": True,
    }
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    result = await repo.get_public(uuid4())

    query_text = str(mock_session.execute.call_args.args[0])
    assert "access_token_encrypted" not in query_text
    assert "app_secret_encrypted" not in query_text
    assert result is not None
    assert "waba_id" in result


@pytest.mark.asyncio
async def test_get_decrypted_returns_plaintext_fields() -> None:
    """Verify get_decrypted decrypts both secret columns into plaintext
    access_token/app_secret keys for internal callers (client factory,
    webhook receiver)."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "verify_token": "verify-1",
        "access_token_encrypted": "cipher-access",
        "app_secret_encrypted": "cipher-secret",
        "is_active": True,
    }
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    with patch(
        "app.integrations.whatsapp.repository.decrypt_secret",
        side_effect=lambda v: v.replace("cipher", "plain"),
    ):
        result = await repo.get_decrypted(uuid4())

    assert result is not None
    assert result["access_token"] == "plain-access"
    assert result["app_secret"] == "plain-secret"
    assert "access_token_encrypted" not in result
    assert "app_secret_encrypted" not in result


@pytest.mark.asyncio
async def test_get_decrypted_returns_none_when_no_config() -> None:
    """Verify a tenant with no config row returns None, not an error --
    callers (factory, webhook router) turn this into a clean 404."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = None
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    result = await repo.get_decrypted(uuid4())

    assert result is None
