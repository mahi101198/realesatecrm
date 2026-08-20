"""Meta WhatsApp Cloud API client (Graph API v21.0), per-tenant.

Ported from the reference implementation in the (read-only, unmodified)
whatsapp_busness_dashboard repository's lib/whatsapp/client.ts -- same
request shapes, same "a 200 response is not automatically a confirmed
send" parsing rule, adapted to httpx/async Python. No auto-retry: sending
a WhatsApp message is not idempotent on Meta's side.
"""

import logging
from typing import Any, Literal

import httpx

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

WhatsAppMessageType = Literal[
    "template", "text", "image", "video", "audio", "document", "location", "interactive",
    "contacts", "reaction",
]


def _to_recipient(phone: str) -> str:
    """Meta's `to` field is country-code-prefixed with NO leading `+`, while
    this codebase's customers.phone is stored as normalized E.164 WITH a
    leading `+`. Strip it here at the integration boundary."""
    return phone.lstrip("+")


class MetaWhatsAppClient:
    """Per-tenant Meta WhatsApp Cloud API client. One instance per request,
    built by app.integrations.whatsapp.factory.get_client_for_tenant --
    never constructed directly outside tests."""

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        waba_id: str,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._waba_id = waba_id
        self._timeout = timeout or _DEFAULT_TIMEOUT

    async def send_text_message(
        self, *, to: str, body: str, context_message_id: str | None = None
    ) -> dict[str, Any]:
        """Send a free-form text message. ONLY works within 24 hours of the
        customer's last inbound message -- callers must check that window
        themselves (see app/whatsapp/service.py) BEFORE calling this."""
        return await self.send_message(
            to=to,
            message_type="text",
            message={"body": body},
            context_message_id=context_message_id,
        )

    async def send_message(
        self,
        *,
        to: str,
        message_type: WhatsAppMessageType,
        message: dict[str, Any],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send any non-template message type."""
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": _to_recipient(to),
            "type": message_type,
            message_type: message,
        }
        if context_message_id:
            body["context"] = {"message_id": context_message_id}
        return await self._send(body)

    async def send_template_message(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        components: list[dict[str, Any]],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a template message. Works anytime -- approved templates are
        not subject to the 24-hour session-window rule."""
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": _to_recipient(to),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components,
            },
        }
        if context_message_id:
            body["context"] = {"message_id": context_message_id}
        return await self._send(body)

    async def list_templates(self, refresh: bool = False) -> list[dict[str, Any]]:  # noqa: ARG002
        """GET .../{waba_id}/message_templates -- list approved/pending/
        rejected templates."""
        url = f"{_GRAPH_API_BASE}/{self._waba_id}/message_templates"
        params = {"fields": "name,status,category,language,components"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http_client:
                response = await http_client.get(
                    url, params=params, headers={"Authorization": f"Bearer {self._access_token}"}
                )
        except httpx.TimeoutException as e:
            raise ExternalServiceError(
                message="Meta WhatsApp API did not respond in time.", code="WHATSAPP_TIMEOUT"
            ) from e
        except httpx.HTTPError as e:
            raise ExternalServiceError(
                message="Could not reach Meta WhatsApp API.", code="WHATSAPP_UNREACHABLE"
            ) from e

        if response.status_code == 200:
            try:
                return list(response.json()["data"])
            except (ValueError, KeyError, TypeError) as e:
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned an unexpected response shape.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=f"Meta WhatsApp API returned an unexpected status ({response.status_code}).",
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    async def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{_GRAPH_API_BASE}/{self._phone_number_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http_client:
                response = await http_client.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as e:
            raise ExternalServiceError(
                message="Meta WhatsApp API did not respond in time.", code="WHATSAPP_TIMEOUT"
            ) from e
        except httpx.HTTPError as e:
            raise ExternalServiceError(
                message="Could not reach Meta WhatsApp API.", code="WHATSAPP_UNREACHABLE"
            ) from e
        return self._parse_send_response(response)

    def _parse_send_response(self, response: httpx.Response) -> dict[str, Any]:
        """A 200 status does NOT guarantee the message was sent -- the only
        real success signal is messages[0].id being present."""
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as e:
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned an unparseable response.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e
            try:
                message_id = payload["messages"][0]["id"]
                if not message_id:
                    raise KeyError("messages[0].id empty")
                return {"message_id": message_id}
            except (KeyError, IndexError, TypeError) as e:
                logger.error(
                    f"WhatsApp send returned 200 but no confirmed message id: {response.text!r}"
                )
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned 200 but did not confirm message was sent.",
                    code="WHATSAPP_SEND_NOT_CONFIRMED",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=f"Meta WhatsApp API returned an unexpected status ({response.status_code}).",
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    def _raise_for_error_status(self, response: httpx.Response) -> None:
        if response.status_code == 400:
            detail = _safe_error_message(response)
            raise ValidationError(
                message=f"Meta rejected the WhatsApp request: {detail}",
                code="WHATSAPP_INVALID_REQUEST",
            )
        if response.status_code in (401, 403):
            raise ExternalServiceError(
                message="Meta WhatsApp API rejected our credentials.", code="WHATSAPP_UNAUTHORIZED"
            )
        if response.status_code == 404:
            raise NotFoundError(
                message="Meta WhatsApp resource not found (check phone_number_id/waba_id).",
                code="WHATSAPP_RESOURCE_NOT_FOUND",
            )
        if response.status_code >= 500:
            raise ExternalServiceError(
                message="Meta WhatsApp API failed to process the request.",
                code="WHATSAPP_PROVIDER_ERROR",
            )


def _safe_error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", response.text))
    except ValueError:
        return response.text
