"""Webhook authenticity checks for the two Superfone webhook streams this
backend still uses (SFVoPI call callbacks and CRM event notifications).
Superfone documents NO HMAC signature verification for either.

  - SFVoPI (answer/ring/hangup): Superfone cannot be configured to add a
    custom auth header to these particular callbacks (they are set as plain
    URLs on the initiate-call request body: answer_url/ring_url/hangup_url).
    Since we control what URL we register, we embed our own shared-secret
    token as a query parameter and validate it server-side.
  - CRM event notifications: configured entirely via Superfone's dashboard
    automations UI, which DOES support a custom `Authorization: Bearer
    <secret>` header per their own documented recommendation. We validate
    that header server-side.

All checks fail closed: missing/invalid credential -> reject before any DB
write (skills/system.md rule #88).
"""

import hmac

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import extract_bearer_token


def verify_sfvopi_webhook_token(token: str | None) -> None:
    """Validate the shared-secret token embedded in our own registered
    answer_url/ring_url/hangup_url query string."""
    expected = settings.SUPERFONE_WEBHOOK_SHARED_SECRET.get_secret_value()
    if not expected:
        # Misconfigured deployment: no secret configured means we cannot
        # verify anything -- fail closed rather than accept unauthenticated.
        raise UnauthorizedError(
            message="Superfone webhook authentication is not configured.",
            code="SFVOPI_WEBHOOK_NOT_CONFIGURED",
        )
    if not token or not hmac.compare_digest(token, expected):
        raise UnauthorizedError(
            message="Invalid or missing Superfone webhook token.",
            code="SFVOPI_WEBHOOK_INVALID_TOKEN",
        )


def verify_superfone_crm_bearer(authorization_header: str | None) -> None:
    """Validate the Authorization: Bearer header configured in Superfone's
    dashboard automations UI for CRM event notifications."""
    expected = settings.SUPERFONE_CRM_WEBHOOK_BEARER_SECRET.get_secret_value()
    if not expected:
        raise UnauthorizedError(
            message="Superfone CRM webhook authentication is not configured.",
            code="SUPERFONE_CRM_WEBHOOK_NOT_CONFIGURED",
        )
    token = extract_bearer_token(authorization_header)
    if not hmac.compare_digest(token, expected):
        raise UnauthorizedError(
            message="Invalid Superfone CRM webhook bearer token.",
            code="SUPERFONE_CRM_WEBHOOK_INVALID_TOKEN",
        )
