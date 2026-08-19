"""Superfone HTTP Clients: SFVoPI (AI voice) and CRM (click-to-call).

Isolated per skills/system.md rule #31: all raw HTTP requests, authentication,
timeouts, and provider-error parsing for Superfone live here. Nothing outside
this module should import `httpx` to talk to Superfone.

Neither client retries automatically (system.md rule #33): initiating an
outbound call and placing a click-to-call bridge are both non-idempotent on
Superfone's side (no idempotency key mechanism is documented), so a blind
retry could place a second real phone call. Callers that want a retry policy
must implement it themselves, deliberately, above this layer.
"""

import logging
from typing import Any, Literal

import httpx

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Explicit connect/read/write/pool timeouts on every request (system.md rule #32).
# Superfone's answer_url deadline is 10s; our own outbound requests to Superfone
# get a slightly more generous budget since they are not on that critical path.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

CallAction = Literal["START", "END"]


class SFVoPIClient:
    """Client for Superfone's SFVoPI (AI voice) API.

    Base: https://prod-api.superfone.co.in/superfone/sfvopi/
    Auth: X-API-Key header.
    """

    def __init__(self, api_key: str, base_url: str, timeout: httpx.Timeout | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT

    async def initiate_outbound_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str = "POST",
        ring_url: str | None = None,
        ring_method: str = "POST",
        hangup_url: str | None = None,
        hangup_method: str = "POST",
        call_time_limit: int = 14400,
        ring_timeout: int = 60,
    ) -> dict[str, Any]:
        """POST {base}/calls -- place an outbound AI call.

        Returns {"request_uuid": str, "status": str} on success.
        Raises ExternalServiceError / ValidationError on documented failure
        responses; never retries.
        """
        if not 1 <= call_time_limit <= 86400:
            raise ValidationError(
                message="call_time_limit must be between 1 and 86400 seconds.",
                code="INVALID_CALL_TIME_LIMIT",
            )
        if not 1 <= ring_timeout <= 600:
            raise ValidationError(
                message="ring_timeout must be between 1 and 600 seconds.",
                code="INVALID_RING_TIMEOUT",
            )

        body: dict[str, Any] = {
            "from": from_number,
            "to": to_number,
            "answer_url": answer_url,
            "answer_method": answer_method,
            "call_time_limit": call_time_limit,
            "ring_timeout": ring_timeout,
        }
        if ring_url:
            body["ring_url"] = ring_url
            body["ring_method"] = ring_method
        if hangup_url:
            body["hangup_url"] = hangup_url
            body["hangup_method"] = hangup_method

        response = await self._post("/calls", body)
        return self._parse_initiate_call_response(response, from_number)

    async def _post(self, path: str, json_body: dict[str, Any]) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.post(
                    url,
                    json=json_body,
                    headers={"X-API-Key": self._api_key, "Content-Type": "application/json"},
                )
        except httpx.TimeoutException as e:
            logger.error(f"SFVoPI request timed out: {path}")
            raise ExternalServiceError(
                message="Superfone SFVoPI did not respond in time.",
                code="SFVOPI_TIMEOUT",
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"SFVoPI request failed: {path}: {e!s}")
            raise ExternalServiceError(
                message="Could not reach Superfone SFVoPI.",
                code="SFVOPI_UNREACHABLE",
            ) from e

    def _parse_initiate_call_response(
        self, response: httpx.Response, from_number: str
    ) -> dict[str, Any]:
        if response.status_code == 200:
            try:
                payload = response.json()
                data = payload["data"]
                return {"request_uuid": data["request_uuid"], "status": data["status"]}
            except (ValueError, KeyError, TypeError) as e:
                logger.error(f"SFVoPI returned an unparseable success response: {response.text!r}")
                raise ExternalServiceError(
                    message="Superfone SFVoPI returned an unexpected response shape.",
                    code="SFVOPI_MALFORMED_RESPONSE",
                ) from e

        if response.status_code == 400:
            detail = _safe_error_message(response)
            if "not linked" in detail.lower():
                raise ExternalServiceError(
                    message=f"VoIP number '{from_number}' is not linked to any SFVoPI app.",
                    code="SFVOPI_FROM_NUMBER_NOT_LINKED",
                )
            raise ValidationError(
                message=f"Superfone rejected the outbound call request: {detail}",
                code="SFVOPI_INVALID_REQUEST",
            )

        if response.status_code == 401:
            raise ExternalServiceError(
                message="Superfone SFVoPI rejected our API credentials.",
                code="SFVOPI_UNAUTHORIZED",
            )

        if response.status_code == 500:
            raise ExternalServiceError(
                message="Superfone SFVoPI failed to initiate the outbound call.",
                code="SFVOPI_INITIATE_FAILED",
            )

        logger.error(f"SFVoPI returned unexpected status {response.status_code}: {response.text!r}")
        raise ExternalServiceError(
            message=f"Superfone SFVoPI returned an unexpected status ({response.status_code}).",
            code="SFVOPI_UNEXPECTED_RESPONSE",
        )


class SuperfoneCRMClient:
    """Client for Superfone's CRM API (human-to-human click-to-call).

    Base: https://prod-api.superfone.co.in/superfone/api/
    Auth: x-api-key header.
    Rate limits (enforced by Superfone, not us): 100 req/min, 1000 req/hour
    per API key -- callers that place click-to-call frequently should apply
    their own throttling above this layer if that ever becomes a concern.
    """

    def __init__(self, api_key: str, base_url: str, timeout: httpx.Timeout | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT

    async def click_to_call(
        self,
        *,
        customer_number: str,
        user_number: str,
        call_action: CallAction = "START",
    ) -> dict[str, Any]:
        """GET {base}/customer/click-2-call -- bridge a staff member's registered
        Superfone phone to a customer's number.

        Returns {"notification_id": ..., "sent_success": bool, "request_uuid": str | None}.
        request_uuid is only present for call_action="START".
        """
        url = f"{self._base_url}/customer/click-2-call"
        params = {
            "customer_number": customer_number,
            "user_number": user_number,
            "call_action": call_action,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    url, params=params, headers={"x-api-key": self._api_key}
                )
        except httpx.TimeoutException as e:
            logger.error("Superfone CRM click-to-call request timed out")
            raise ExternalServiceError(
                message="Superfone CRM did not respond in time.",
                code="SUPERFONE_CRM_TIMEOUT",
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"Superfone CRM click-to-call request failed: {e!s}")
            raise ExternalServiceError(
                message="Could not reach Superfone CRM.",
                code="SUPERFONE_CRM_UNREACHABLE",
            ) from e

        return self._parse_click_to_call_response(response, user_number)

    def _parse_click_to_call_response(
        self, response: httpx.Response, user_number: str
    ) -> dict[str, Any]:
        if response.status_code == 200:
            try:
                payload = response.json()
                data = payload["data"]
                return {
                    "notification_id": data.get("notificationID"),
                    "sent_success": bool(data.get("sentSuccess")),
                    "request_uuid": data.get("request_uuid"),
                }
            except (ValueError, KeyError, TypeError) as e:
                logger.error(f"Superfone CRM returned an unparseable response: {response.text!r}")
                raise ExternalServiceError(
                    message="Superfone CRM returned an unexpected response shape.",
                    code="SUPERFONE_CRM_MALFORMED_RESPONSE",
                ) from e

        if response.status_code == 400:
            detail = _safe_error_message(response)
            raise ValidationError(
                message=f"Superfone rejected the click-to-call request: {detail}",
                code="SUPERFONE_CRM_INVALID_REQUEST",
            )

        if response.status_code == 401:
            raise ExternalServiceError(
                message="Superfone CRM rejected our API credentials.",
                code="SUPERFONE_CRM_UNAUTHORIZED",
            )

        if response.status_code == 404:
            raise NotFoundError(
                message=(
                    f"Staff phone '{user_number}' is not registered for Superfone "
                    "Dashboard Calling."
                ),
                code="SUPERFONE_USER_NOT_REGISTERED",
            )

        if response.status_code == 500:
            raise ExternalServiceError(
                message="Superfone CRM failed to place the click-to-call request.",
                code="SUPERFONE_CRM_FAILED",
            )

        logger.error(
            f"Superfone CRM returned unexpected status {response.status_code}: {response.text!r}"
        )
        raise ExternalServiceError(
            message=f"Superfone CRM returned an unexpected status ({response.status_code}).",
            code="SUPERFONE_CRM_UNEXPECTED_RESPONSE",
        )


def _safe_error_message(response: httpx.Response) -> str:
    """Best-effort extraction of a human-readable error detail from a
    Superfone error response, without ever raising on a malformed body."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("error") or response.text)
    except ValueError:
        pass
    return response.text or f"HTTP {response.status_code}"
