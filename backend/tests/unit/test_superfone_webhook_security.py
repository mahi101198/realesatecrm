"""Unit tests for Superfone webhook authenticity checks (fail-closed).

Superfone documents no HMAC for either webhook stream, so these are this
project's own defense-in-depth: a URL-embedded shared-secret token for
SFVoPI, and a per-tenant dashboard-configured Authorization: Bearer header
for CRM event notifications (see migration 033,
superfone_crm_tenant_configs). Both must reject BEFORE any DB write on a
bad/missing credential.
"""

from unittest.mock import patch

import pytest

from app.core.exceptions import UnauthorizedError
from app.webhooks.superfone.security import (
    hash_secret,
    verify_sfvopi_webhook_token,
    verify_superfone_crm_bearer,
)


class _FakeSecretStr:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def test_verify_sfvopi_token_accepts_matching_token() -> None:
    """Verify a correct token passes without raising."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET = _FakeSecretStr("correct-secret")
        verify_sfvopi_webhook_token("correct-secret")  # must not raise


def test_verify_sfvopi_token_rejects_wrong_token() -> None:
    """Verify a wrong token is rejected."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET = _FakeSecretStr("correct-secret")
        with pytest.raises(UnauthorizedError) as exc_info:
            verify_sfvopi_webhook_token("wrong-secret")
    assert exc_info.value.code == "SFVOPI_WEBHOOK_INVALID_TOKEN"
    assert exc_info.value.status_code == 401


def test_verify_sfvopi_token_rejects_missing_token() -> None:
    """Verify a missing token is rejected."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET = _FakeSecretStr("correct-secret")
        with pytest.raises(UnauthorizedError) as exc_info:
            verify_sfvopi_webhook_token(None)
    assert exc_info.value.code == "SFVOPI_WEBHOOK_INVALID_TOKEN"


def test_verify_sfvopi_token_fails_closed_when_unconfigured() -> None:
    """Verify a deployment with no secret configured rejects everything
    rather than accepting unauthenticated requests."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET = _FakeSecretStr("")
        with pytest.raises(UnauthorizedError) as exc_info:
            verify_sfvopi_webhook_token("anything")
    assert exc_info.value.code == "SFVOPI_WEBHOOK_NOT_CONFIGURED"


def test_verify_crm_bearer_accepts_matching_token() -> None:
    """Verify a correct Authorization: Bearer header passes, checked against
    this tenant's own stored hash (not a global setting)."""
    verify_superfone_crm_bearer("Bearer crm-secret", hash_secret("crm-secret"))  # must not raise


def test_verify_crm_bearer_rejects_wrong_token() -> None:
    """Verify a wrong bearer token is rejected."""
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_superfone_crm_bearer("Bearer wrong-secret", hash_secret("crm-secret"))
    assert exc_info.value.code == "SUPERFONE_CRM_WEBHOOK_INVALID_TOKEN"


def test_verify_crm_bearer_rejects_missing_header() -> None:
    """Verify a missing Authorization header is rejected."""
    with pytest.raises(UnauthorizedError):
        verify_superfone_crm_bearer(None, hash_secret("crm-secret"))


def test_verify_crm_bearer_rejects_malformed_header() -> None:
    """Verify a header that isn't 'Bearer <token>' shaped is rejected."""
    with pytest.raises(UnauthorizedError):
        verify_superfone_crm_bearer("crm-secret", hash_secret("crm-secret"))


def test_verify_crm_bearer_rejects_when_tenant_has_no_config() -> None:
    """Verify a tenant with no (or an inactive) config row is rejected --
    same code as a genuinely unconfigured deployment, identical to a wrong
    token from the caller's point of view (no account-enumeration signal)."""
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_superfone_crm_bearer("Bearer crm-secret", None)
    assert exc_info.value.code == "SUPERFONE_CRM_WEBHOOK_NOT_CONFIGURED"


def test_hash_secret_is_deterministic_and_distinguishes_inputs() -> None:
    """Verify hash_secret is a pure, deterministic one-way function -- the
    repository (on write) and security check (on read) must always agree."""
    assert hash_secret("same-secret") == hash_secret("same-secret")
    assert hash_secret("secret-a") != hash_secret("secret-b")
