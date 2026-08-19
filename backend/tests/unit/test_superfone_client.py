"""Unit tests for the Superfone SFVoPI and CRM HTTP clients.

The HTTP layer (httpx.AsyncClient) is mocked; these tests cover the
documented success shape and every documented error response for both
initiate_outbound_call and click_to_call, verifying each maps to a clean,
specific exception rather than a raw HTTP error leaking to callers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.integrations.superfone.client import SFVoPIClient, SuperfoneCRMClient


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text or (str(json_body) if json_body else "")
    return response


def _patched_async_client(response: MagicMock) -> AsyncMock:
    """Build a mock httpx.AsyncClient context manager whose .post()/.get()
    return `response`."""
    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.get.return_value = response
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_instance
    client_cm.__aexit__.return_value = False
    return client_cm


# ---------------------------------------------------------------------------
# SFVoPIClient.initiate_outbound_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_outbound_call_success() -> None:
    """Verify the documented 200 success shape is parsed correctly."""
    response = _mock_response(
        200, {"data": {"request_uuid": "sfv_ob_req_123", "status": "queued"}, "message": "success"}
    )
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        result = await client.initiate_outbound_call(
            from_number="+911111111111",
            to_number="+912222222222",
            answer_url="https://app.test/answer",
        )

    assert result == {"request_uuid": "sfv_ob_req_123", "status": "queued"}


@pytest.mark.asyncio
async def test_initiate_outbound_call_rejects_out_of_range_call_time_limit() -> None:
    """Verify call_time_limit outside [1, 86400] is rejected before any HTTP call."""
    client = SFVoPIClient(api_key="k", base_url="https://example.test")
    with pytest.raises(ValidationError) as exc_info:
        await client.initiate_outbound_call(
            from_number="+91",
            to_number="+91",
            answer_url="https://app.test/answer",
            call_time_limit=999999,
        )
    assert exc_info.value.code == "INVALID_CALL_TIME_LIMIT"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_number_not_linked_error() -> None:
    """Verify the documented 400 'not linked' error maps to a specific ExternalServiceError."""
    response = _mock_response(
        400, {"message": "VoIP number +911111111111 is not linked to any SFVoPI app"}
    )
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+911111111111", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_FROM_NUMBER_NOT_LINKED"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_generic_400_to_validation_error() -> None:
    """Verify a generic 400 (not the 'not linked' case) maps to ValidationError."""
    response = _mock_response(400, {"message": "Invalid request body"})
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ValidationError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+91", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_INVALID_REQUEST"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_401_to_external_service_error() -> None:
    """Verify a 401 maps to ExternalServiceError (our credential problem, not caller's)."""
    response = _mock_response(401, {"message": "unauthorized"})
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+91", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_UNAUTHORIZED"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_500_to_external_service_error() -> None:
    """Verify a 500 maps to ExternalServiceError."""
    response = _mock_response(500, {"message": "Failed to initiate outbound call"})
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+91", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_INITIATE_FAILED"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_timeout_to_external_service_error() -> None:
    """Verify a timeout maps to a clean ExternalServiceError, not a raw exception."""
    client = SFVoPIClient(api_key="k", base_url="https://example.test")
    client_cm = AsyncMock()
    client_cm.__aenter__.side_effect = httpx.TimeoutException("timed out")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = client_cm
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+91", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_TIMEOUT"


@pytest.mark.asyncio
async def test_initiate_outbound_call_maps_malformed_success_body() -> None:
    """Verify a 200 with an unexpected body shape maps cleanly, not a raw KeyError."""
    response = _mock_response(200, {"unexpected": "shape"})
    client = SFVoPIClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.initiate_outbound_call(
                from_number="+91", to_number="+91", answer_url="https://app.test/answer"
            )
    assert exc_info.value.code == "SFVOPI_MALFORMED_RESPONSE"


# ---------------------------------------------------------------------------
# SuperfoneCRMClient.click_to_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_to_call_success() -> None:
    """Verify the documented 200 success shape is parsed correctly."""
    response = _mock_response(
        200,
        {
            "data": {"notificationID": "n1", "sentSuccess": True, "request_uuid": "c2c_req_1"},
            "message": "success",
        },
    )
    client = SuperfoneCRMClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        result = await client.click_to_call(customer_number="+911", user_number="+912")

    assert result == {"notification_id": "n1", "sent_success": True, "request_uuid": "c2c_req_1"}


@pytest.mark.asyncio
async def test_click_to_call_maps_404_to_not_found_error() -> None:
    """Verify the documented 404 'User not found' maps to NotFoundError."""
    response = _mock_response(404, {"message": "User not found"})
    client = SuperfoneCRMClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(NotFoundError) as exc_info:
            await client.click_to_call(customer_number="+911", user_number="+912")
    assert exc_info.value.code == "SUPERFONE_USER_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_click_to_call_maps_400_to_validation_error() -> None:
    """Verify a 400 maps to ValidationError."""
    response = _mock_response(400, {"message": "invalid request"})
    client = SuperfoneCRMClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ValidationError):
            await client.click_to_call(customer_number="+911", user_number="+912")


@pytest.mark.asyncio
async def test_click_to_call_maps_500_to_external_service_error() -> None:
    """Verify a 500 maps to ExternalServiceError."""
    response = _mock_response(500, {"message": "internal error"})
    client = SuperfoneCRMClient(api_key="k", base_url="https://example.test")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = _patched_async_client(response)
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.click_to_call(customer_number="+911", user_number="+912")
    assert exc_info.value.code == "SUPERFONE_CRM_FAILED"


@pytest.mark.asyncio
async def test_click_to_call_maps_connection_error() -> None:
    """Verify a connection-level failure maps to a clean ExternalServiceError."""
    client = SuperfoneCRMClient(api_key="k", base_url="https://example.test")
    client_cm = AsyncMock()
    client_cm.__aenter__.side_effect = httpx.ConnectError("boom")

    with patch("app.integrations.superfone.client.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value = client_cm
        with pytest.raises(ExternalServiceError) as exc_info:
            await client.click_to_call(customer_number="+911", user_number="+912")
    assert exc_info.value.code == "SUPERFONE_CRM_UNREACHABLE"
