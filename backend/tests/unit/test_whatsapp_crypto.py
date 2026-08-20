"""Unit tests for Fernet-based encryption of WhatsApp tenant credentials."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.core.exceptions import BusinessRuleError
from app.integrations.whatsapp.crypto import decrypt_secret, encrypt_secret


class _FakeSecretStr:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _valid_key() -> str:
    return Fernet.generate_key().decode()


def test_encrypt_then_decrypt_roundtrips() -> None:
    """Verify a value encrypted with encrypt_secret decrypts back to the
    original plaintext with decrypt_secret."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr(_valid_key())
        ciphertext = encrypt_secret("my-access-token")
        assert ciphertext != "my-access-token"
        assert decrypt_secret(ciphertext) == "my-access-token"


def test_encrypt_fails_closed_when_key_unconfigured() -> None:
    """Verify encryption refuses to run with an empty encryption key rather
    than silently storing plaintext or crashing with an obscure error."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr("")
        with pytest.raises(BusinessRuleError) as exc_info:
            encrypt_secret("my-access-token")
    assert exc_info.value.code == "WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED"


def test_decrypt_fails_closed_when_key_unconfigured() -> None:
    """Same fail-closed behavior on the decrypt path."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr("")
        with pytest.raises(BusinessRuleError) as exc_info:
            decrypt_secret("anything")
    assert exc_info.value.code == "WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED"
