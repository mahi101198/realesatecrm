"""Unit tests for the whatsapp-dashboard call-agent bearer check. Mirrors
tests/unit/test_superfone_webhook_security.py's verify_superfone_crm_bearer
coverage exactly."""

from unittest.mock import patch

import pytest

from app.core.exceptions import UnauthorizedError
from app.webhooks.whatsapp_dashboard.security import verify_call_agent_bearer


class _FakeSecretStr:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def test_accepts_matching_token() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        verify_call_agent_bearer("Bearer secret-1")  # must not raise


def test_rejects_wrong_token() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer("Bearer wrong")


def test_rejects_missing_header() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer(None)


def test_fails_closed_when_unconfigured() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer("Bearer anything")
