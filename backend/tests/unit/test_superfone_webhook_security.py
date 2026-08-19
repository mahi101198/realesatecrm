"""Unit tests for Superfone webhook authenticity checks (fail-closed).

Superfone documents no HMAC for either webhook stream, so these are this
project's own defense-in-depth: a URL-embedded shared-secret token for
SFVoPI, and a dashboard-configured Authorization: Bearer header for CRM
event notifications. Both must reject BEFORE any DB write on a bad/missing
credential.
"""

from unittest.mock import patch

import pytest

from app.core.exceptions import UnauthorizedError
from app.webhooks.superfone.security import (
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
    """Verify a correct Authorization: Bearer header passes."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_CRM_WEBHOOK_BEARER_SECRET = _FakeSecretStr("crm-secret")
        verify_superfone_crm_bearer("Bearer crm-secret")  # must not raise


def test_verify_crm_bearer_rejects_wrong_token() -> None:
    """Verify a wrong bearer token is rejected."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_CRM_WEBHOOK_BEARER_SECRET = _FakeSecretStr("crm-secret")
        with pytest.raises(UnauthorizedError) as exc_info:
            verify_superfone_crm_bearer("Bearer wrong-secret")
    assert exc_info.value.code == "SUPERFONE_CRM_WEBHOOK_INVALID_TOKEN"


def test_verify_crm_bearer_rejects_missing_header() -> None:
    """Verify a missing Authorization header is rejected."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_CRM_WEBHOOK_BEARER_SECRET = _FakeSecretStr("crm-secret")
        with pytest.raises(UnauthorizedError):
            verify_superfone_crm_bearer(None)


def test_verify_crm_bearer_rejects_malformed_header() -> None:
    """Verify a header that isn't 'Bearer <token>' shaped is rejected."""
    with patch("app.webhooks.superfone.security.settings") as mock_settings:
        mock_settings.SUPERFONE_CRM_WEBHOOK_BEARER_SECRET = _FakeSecretStr("crm-secret")
        with pytest.raises(UnauthorizedError):
            verify_superfone_crm_bearer("crm-secret")
