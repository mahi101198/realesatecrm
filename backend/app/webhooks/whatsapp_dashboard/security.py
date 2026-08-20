"""Bearer-token check for the whatsapp_busness_dashboard product's
call-agent trigger requests. That product is a separate, unmodified
repository whose stub `triggerCallAgent` posts a static Authorization:
Bearer header -- this mirrors
app/webhooks/superfone/security.py::verify_superfone_crm_bearer exactly."""

import hmac

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import extract_bearer_token


def verify_call_agent_bearer(authorization_header: str | None) -> None:
    """Validate the Authorization: Bearer header against
    WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET. Fails closed."""
    expected = settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET.get_secret_value()
    if not expected:
        raise UnauthorizedError(
            message="WhatsApp dashboard call-agent authentication is not configured.",
            code="CALL_AGENT_WEBHOOK_NOT_CONFIGURED",
        )
    token = extract_bearer_token(authorization_header)
    if not hmac.compare_digest(token, expected):
        raise UnauthorizedError(
            message="Invalid WhatsApp dashboard call-agent bearer token.",
            code="CALL_AGENT_WEBHOOK_INVALID_TOKEN",
        )
