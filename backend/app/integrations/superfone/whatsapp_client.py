"""Superfone WhatsApp Business API Client ("Dragonfly" -- internal codename
visible in the URL path: superfone/api/dragonfly/whatsapp/...).

Isolated per skills/system.md rule #31, in its own file (not client.py)
because this is a genuinely distinct product surface with its own payload
shapes and its own critical trap (see _parse_send_response below), even
though it shares client.py's timeout/error-message helpers and, per the
judgment call documented below, its credentials.

CREDENTIAL ASSUMPTION (flag for verification against a real account before
production use): this client reuses SUPERFONE_CRM_API_KEY / SUPERFONE_CRM_BASE_URL
rather than introducing a third credential pair. Reasoning: the documented
endpoint is `{crm_base}/dragonfly/whatsapp/messages`, i.e. nested under the
SAME `superfone/api/` base path as click-to-call, not under SFVoPI's
`superfone/sfvopi/` base -- suggesting WhatsApp (Dragonfly) is part of the
same "CRM API" product surface Superfone issues one key for. This was not
independently confirmed against a live account. If wrong, splitting this
out is a one-line change: add SUPERFONE_WHATSAPP_API_KEY to config and wire
it in factory.py's get_superfone_whatsapp_client().

No auto-retry (system.md rule #33): sending a WhatsApp message is not
idempotent on Superfone's/Meta's side (no idempotency key mechanism is
documented), so a blind retry could send a duplicate message to a customer.
"""

import logging
from typing import Any, Literal

import httpx

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.integrations.superfone.client import _DEFAULT_TIMEOUT, _safe_error_message

logger = logging.getLogger(__name__)

WhatsAppMessageType = Literal[
    "template",
    "text",
    "image",
    "video",
    "audio",
    "document",
    "location",
    "interactive",
    "contacts",
    "reaction",
]


class SuperfoneWhatsAppClient:
    """Client for Superfone's WhatsApp Business API (Dragonfly).

    Base: https://prod-api.superfone.co.in/superfone/api/ (dragonfly/whatsapp/... sub-path)
    Auth: x-api-key header.
    """

    def __init__(self, api_key: str, base_url: str, timeout: httpx.Timeout | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT

    async def send_template_message(
        self,
        *,
        recipient: str,
        template_name: str,
        language: str,
        components: list[dict[str, Any]],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a template message. Works anytime (approved templates are not
        subject to the 24-hour session-window rule that applies to free-form
        message types)."""
        body: dict[str, Any] = {
            "recipient": recipient,
            "type": "template",
            "templateName": template_name,
            "language": language,
            "components": components,
        }
        if context_message_id:
            body["context"] = {"message_id": context_message_id}
        return await self._send(body)

    async def send_text_message(
        self, *, recipient: str, body: str, context_message_id: str | None = None
    ) -> dict[str, Any]:
        """Send a free-form text message. ONLY works within 24 hours of the
        customer's last inbound message (WhatsApp session-window rule) --
        callers must check that window themselves (see
        app/whatsapp/service.py) BEFORE calling this; Meta will reject it
        outside the window regardless."""
        return await self.send_message(
            recipient=recipient,
            message_type="text",
            message={"body": body},
            context_message_id=context_message_id,
        )

    async def send_message(
        self,
        *,
        recipient: str,
        message_type: WhatsAppMessageType,
        message: dict[str, Any],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send any non-template message type (text/image/video/audio/
        document/location/interactive/contacts/reaction). Subject to the
        24-hour session-window rule -- see send_text_message."""
        request_body: dict[str, Any] = {
            "recipient": recipient,
            "type": message_type,
            "message": message,
        }
        if context_message_id:
            request_body["context"] = {"message_id": context_message_id}
        return await self._send(request_body)

    async def list_templates(self, refresh: bool = False) -> list[dict[str, Any]]:
        """GET .../message_templates -- list approved/pending/rejected templates.
        refresh=True bypasses Superfone's cache and pulls fresh from Meta."""
        url = f"{self._base_url}/dragonfly/whatsapp/message_templates"
        params = {"refresh": "true"} if refresh else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    url, params=params, headers={"x-api-key": self._api_key}
                )
        except httpx.TimeoutException as e:
            logger.error("WhatsApp list_templates request timed out")
            raise ExternalServiceError(
                message="Superfone WhatsApp API did not respond in time.",
                code="WHATSAPP_TIMEOUT",
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"WhatsApp list_templates request failed: {e!s}")
            raise ExternalServiceError(
                message="Could not reach Superfone WhatsApp API.",
                code="WHATSAPP_UNREACHABLE",
            ) from e

        if response.status_code == 200:
            try:
                payload = response.json()
                return list(payload["data"]["data"])
            except (ValueError, KeyError, TypeError) as e:
                logger.error(f"WhatsApp list_templates unparseable response: {response.text!r}")
                raise ExternalServiceError(
                    message="Superfone WhatsApp API returned an unexpected response shape.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=(
                f"Superfone WhatsApp API returned an unexpected status "
                f"({response.status_code})."
            ),
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    async def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/dragonfly/whatsapp/messages"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
                )
        except httpx.TimeoutException as e:
            logger.error("WhatsApp send request timed out")
            raise ExternalServiceError(
                message="Superfone WhatsApp API did not respond in time.",
                code="WHATSAPP_TIMEOUT",
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"WhatsApp send request failed: {e!s}")
            raise ExternalServiceError(
                message="Could not reach Superfone WhatsApp API.",
                code="WHATSAPP_UNREACHABLE",
            ) from e

        return self._parse_send_response(response)

    def _parse_send_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse a send response, handling the critical trap: HTTP 200 does
        NOT guarantee the message was actually sent. A 200 body of
        {"message": "...", "reason": "INSUFFICIENT_BALANCE"} means nothing
        was sent -- the only real success signal is data.messages[0].id
        being present."""
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as e:
                logger.error(f"WhatsApp send returned non-JSON body: {response.text!r}")
                raise ExternalServiceError(
                    message="Superfone WhatsApp API returned an unparseable response.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e

            reason = payload.get("reason")
            if reason == "INSUFFICIENT_BALANCE":
                raise ExternalServiceError(
                    message=(
                        "Superfone WhatsApp wallet has insufficient balance to "
                        "send this message."
                    ),
                    code="WHATSAPP_INSUFFICIENT_BALANCE",
                )

            try:
                messages = payload["data"]["messages"]
                if not messages or not messages[0].get("id"):
                    raise KeyError("messages[0].id missing")
                return {
                    "message_id": messages[0]["id"],
                    "message_status": messages[0].get("message_status"),
                }
            except (KeyError, IndexError, TypeError) as e:
                logger.error(
                    f"WhatsApp send returned 200 but no confirmed message id "
                    f"(reason={reason!r}): {response.text!r}"
                )
                raise ExternalServiceError(
                    message=(
                        "Superfone WhatsApp API returned HTTP 200 but did not confirm "
                        "the message was sent."
                    ),
                    code="WHATSAPP_SEND_NOT_CONFIRMED",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=(
                f"Superfone WhatsApp API returned an unexpected status "
                f"({response.status_code})."
            ),
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    def _raise_for_error_status(self, response: httpx.Response) -> None:
        """Raise for the documented 400/401/5xx error shapes. Always raises
        when called for a non-200 status; returns normally only to let the
        caller's final fallback raise for truly novel status codes."""
        if response.status_code == 400:
            detail = _safe_error_message(response)
            detail_lower = detail.lower()
            if "template not found" in detail_lower:
                raise NotFoundError(
                    message=f"WhatsApp template not found: {detail}",
                    code="WHATSAPP_TEMPLATE_NOT_FOUND",
                )
            if "invalid message type" in detail_lower:
                raise ValidationError(
                    message=f"Invalid WhatsApp message type: {detail}",
                    code="WHATSAPP_INVALID_MESSAGE_TYPE",
                )
            raise ValidationError(
                message=f"Superfone rejected the WhatsApp request: {detail}",
                code="WHATSAPP_INVALID_REQUEST",
            )

        if response.status_code == 401:
            raise ExternalServiceError(
                message="Superfone WhatsApp API rejected our API credentials.",
                code="WHATSAPP_UNAUTHORIZED",
            )

        if response.status_code >= 500:
            raise ExternalServiceError(
                message="Superfone WhatsApp API failed to process the request.",
                code="WHATSAPP_PROVIDER_ERROR",
            )
