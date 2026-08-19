"""Unit tests for MetaWhatsAppClient's request-building and response
parsing. Ported from whatsapp_busness_dashboard's lib/whatsapp/client.ts
reference implementation (read-only reference, no code shared)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.integrations.whatsapp.client import MetaWhatsAppClient


def _client() -> MetaWhatsAppClient:
    return MetaWhatsAppClient(
        phone_number_id="770971286099252",
        access_token="test-access-token",
        waba_id="789424670144149",
    )


@pytest.mark.asyncio
async def test_send_text_message_builds_correct_request() -> None:
    """Verify the request hits graph.facebook.com/v21.0/{phone_number_id}/messages
    with a Bearer auth header and Meta's documented text-message body shape."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "wamid.ABC123"}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        result = await _client().send_text_message(to="+919999999999", body="hello")

    assert result == {"message_id": "wamid.ABC123"}
    call_kwargs = mock_post.call_args.kwargs
    assert mock_post.call_args.args[0] == (
        "https://graph.facebook.com/v21.0/770971286099252/messages"
    )
    assert call_kwargs["headers"]["Authorization"] == "Bearer test-access-token"
    assert call_kwargs["json"]["to"] == "919999999999"
    assert call_kwargs["json"]["type"] == "text"
    assert call_kwargs["json"]["text"] == {"body": "hello"}


@pytest.mark.asyncio
async def test_send_template_message_builds_correct_request() -> None:
    """Verify template sends carry Meta's template payload shape."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "wamid.DEF456"}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        result = await _client().send_template_message(
            to="+919999999999",
            template_name="site_visit_reminder",
            language="hi",
            components=[{"type": "body", "parameters": []}],
        )

    assert result == {"message_id": "wamid.DEF456"}
    body = mock_post.call_args.kwargs["json"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "site_visit_reminder"
    assert body["template"]["language"] == {"code": "hi"}
    assert body["template"]["components"] == [{"type": "body", "parameters": []}]


@pytest.mark.asyncio
async def test_send_message_raises_when_no_message_id_confirmed() -> None:
    """Verify a 200 response with no messages[0].id is treated as an
    unconfirmed send, not a silent success -- mirrors the reference
    implementation's parseSendMessageResponse contract."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": {"message": "rejected"}}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)), pytest.raises(
        ExternalServiceError
    ) as exc_info:
        await _client().send_text_message(to="+919999999999", body="hello")

    assert exc_info.value.code == "WHATSAPP_SEND_NOT_CONFIRMED"


@pytest.mark.asyncio
async def test_send_message_raises_on_timeout() -> None:
    """Verify a timeout maps to a clean ExternalServiceError, not a raw
    httpx exception leaking out of this integration boundary."""
    with patch(
        "httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    ), pytest.raises(ExternalServiceError) as exc_info:
        await _client().send_text_message(to="+919999999999", body="hello")

    assert exc_info.value.code == "WHATSAPP_TIMEOUT"


@pytest.mark.asyncio
async def test_list_templates_parses_data_array() -> None:
    """Verify list_templates hits the WABA's message_templates endpoint and
    returns the `data` array from Meta's response envelope."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"name": "greeting", "status": "APPROVED"}]}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)) as mock_get:
        result = await _client().list_templates()

    assert result == [{"name": "greeting", "status": "APPROVED"}]
    assert mock_get.call_args.args[0] == (
        "https://graph.facebook.com/v21.0/789424670144149/message_templates"
    )
