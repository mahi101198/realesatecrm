"""Fernet-based at-rest encryption for per-tenant Meta WhatsApp credentials
(access_token, app_secret) stored in whatsapp_tenant_configs.

Fails closed: an unconfigured or malformed WHATSAPP_CREDENTIALS_ENCRYPTION_KEY
raises rather than silently storing plaintext or producing an unreadable
cryptography-library stack trace at the call site.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import BusinessRuleError


def _get_fernet() -> Fernet:
    key = settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY.get_secret_value()
    if not key:
        raise BusinessRuleError(
            message=(
                "WhatsApp credential encryption is not configured "
                "(WHATSAPP_CREDENTIALS_ENCRYPTION_KEY is empty)."
            ),
            code="WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED",
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise BusinessRuleError(
            message="WHATSAPP_CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key.",
            code="WHATSAPP_ENCRYPTION_KEY_INVALID",
        ) from e


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret for storage. Returns a Fernet token (str)."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored Fernet token back to plaintext."""
    fernet = _get_fernet()
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise BusinessRuleError(
            message="Stored WhatsApp credential could not be decrypted.",
            code="WHATSAPP_CREDENTIAL_DECRYPT_FAILED",
        ) from e
