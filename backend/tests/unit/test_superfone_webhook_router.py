"""Unit tests for the Superfone webhook router: fail-closed auth (rejected
before any DB write), malformed-payload handling (clean error, not a 500),
and that the answer endpoint always returns valid Stream JSON."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import UnauthorizedError, ValidationError
from app.webhooks.superfone.router import (
    _read_json_body,
    sfvopi_answer_webhook,
    superfone_crm_event_webhook,
)


def _fake_request(json_body: object = None, json_error: Exception | None = None) -> MagicMock:
    request = MagicMock()
    if json_error is not None:
        request.json = AsyncMock(side_effect=json_error)
    else:
        request.json = AsyncMock(return_value=json_body)
    request.headers = {}
    return request


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_json_body_rejects_non_json() -> None:
    """Verify a non-JSON body is a clean ValidationError, not a raw 500."""
    request = _fake_request(json_error=ValueError("not json"))
    with pytest.raises(ValidationError) as exc_info:
        await _read_json_body(request)
    assert exc_info.value.code == "INVALID_WEBHOOK_BODY"


@pytest.mark.asyncio
async def test_read_json_body_rejects_non_object_json() -> None:
    """Verify a JSON body that isn't an object (e.g. a bare list) is rejected cleanly."""
    request = _fake_request(json_body=["not", "an", "object"])
    with pytest.raises(ValidationError) as exc_info:
        await _read_json_body(request)
    assert exc_info.value.code == "INVALID_WEBHOOK_BODY"


@pytest.mark.asyncio
async def test_read_json_body_accepts_valid_object() -> None:
    """Verify a well-formed JSON object passes through unchanged."""
    request = _fake_request(json_body={"call_uuid": "x"})
    body = await _read_json_body(request)
    assert body == {"call_uuid": "x"}


# ---------------------------------------------------------------------------
# Auth fails closed BEFORE any DB write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sfvopi_answer_webhook_rejects_invalid_token_before_db_write() -> None:
    """Verify an invalid token is rejected before the session is ever touched."""
    request = _fake_request(json_body={"call_uuid": "x"})
    session = AsyncMock()

    with patch(
        "app.webhooks.superfone.router.verify_sfvopi_webhook_token",
        side_effect=UnauthorizedError(message="bad token", code="SFVOPI_WEBHOOK_INVALID_TOKEN"),
    ), pytest.raises(UnauthorizedError):
        await sfvopi_answer_webhook(request, token="wrong", session=session)

    session.execute.assert_not_called()
    session.commit.assert_not_called()
    request.json.assert_not_called()  # auth is checked before we even parse the body


@pytest.mark.asyncio
async def test_crm_event_webhook_rejects_invalid_bearer_before_db_write() -> None:
    """Verify a missing/invalid Authorization header is rejected before any DB write."""
    request = _fake_request(json_body={"cdr_uuid": "x"})
    session = AsyncMock()

    with patch(
        "app.webhooks.superfone.router.verify_superfone_crm_bearer",
        side_effect=UnauthorizedError(
            message="bad bearer", code="SUPERFONE_CRM_WEBHOOK_INVALID_TOKEN"
        ),
    ), pytest.raises(UnauthorizedError):
        await superfone_crm_event_webhook("ALL_CALLS", request, session=session)

    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Answer webhook always returns valid Stream JSON, even if bookkeeping fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sfvopi_answer_webhook_returns_stream_json_even_if_processing_fails() -> None:
    """Verify the 10-second-deadline Stream JSON response is still returned
    even when the DB bookkeeping that follows it raises."""
    request = _fake_request(json_body={"call_uuid": "x", "request_uuid": "y"})
    session = AsyncMock()

    with (
        patch("app.webhooks.superfone.router.verify_sfvopi_webhook_token"),
        patch("app.webhooks.superfone.router.SuperfoneWebhookService") as mock_service_cls,
    ):
        mock_service = mock_service_cls.return_value
        mock_service.build_stream_response.return_value.model_dump.return_value = {
            "stream": {"url": "wss://voice-agent.test", "codec": "PCMA", "sample_rate": 8000}
        }
        mock_service.handle_sfvopi_answer = AsyncMock(side_effect=RuntimeError("db exploded"))

        response = await sfvopi_answer_webhook(request, token="valid", session=session)

    assert response.status_code == 200
    session.rollback.assert_awaited_once()
