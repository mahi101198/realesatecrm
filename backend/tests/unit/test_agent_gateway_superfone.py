"""Unit tests for AgentGateway's Superfone SFVoPI call-placement wiring
(_place_sfvopi_call / _fail_placement), added to start_call. Mirrors the
mocking style already established in tests/unit/test_agent_gateway.py."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agent.gateway import AgentGateway
from app.core.exceptions import ExternalServiceError


@pytest.mark.asyncio
async def test_place_sfvopi_call_success_creates_calls_row_and_links_job() -> None:
    """Verify a successful placement creates the `calls` row, links
    call_jobs.call_id to it, and stores request_uuid as the attempt's
    provider_call_id."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    job_row = {"id": uuid4(), "lead_id": uuid4(), "customer_id": uuid4()}
    att_row = {"id": uuid4()}
    call_id = uuid4()

    customer_res = MagicMock()
    customer_res.mappings.return_value.one_or_none.return_value = {"phone": "+919999999999"}
    calls_insert_res = MagicMock()
    calls_insert_res.mappings.return_value.one.return_value = {"id": call_id}
    job_update_res = MagicMock()
    att_update_res = MagicMock()
    att_update_res.mappings.return_value.one.return_value = {
        **att_row,
        "provider_call_id": "sfv_ob_req_1",
    }
    mock_session.execute.side_effect = [
        customer_res,
        calls_insert_res,
        job_update_res,
        att_update_res,
    ]

    fake_client = AsyncMock()
    fake_client.initiate_outbound_call = AsyncMock(
        return_value={"request_uuid": "sfv_ob_req_1", "status": "queued"}
    )

    with (
        patch("app.agent.gateway.get_sfvopi_client", return_value=fake_client),
        patch("app.agent.gateway.settings") as mock_settings,
    ):
        mock_settings.SUPERFONE_SFVOPI_FROM_NUMBER = "+911111111111"
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET.get_secret_value.return_value = "secret"
        mock_settings.APP_PUBLIC_BASE_URL = "https://app.test"

        result = await gateway._place_sfvopi_call(tenant_id, job_row, att_row)

    assert result["success"] is True
    assert result["attempt"]["provider_call_id"] == "sfv_ob_req_1"
    fake_client.initiate_outbound_call.assert_awaited_once()
    call_kwargs = fake_client.initiate_outbound_call.call_args.kwargs
    assert call_kwargs["from_number"] == "+911111111111"
    assert call_kwargs["to_number"] == "+919999999999"
    assert "secret" in call_kwargs["answer_url"]


@pytest.mark.asyncio
async def test_place_sfvopi_call_fails_cleanly_when_from_number_unconfigured() -> None:
    """Verify a missing SUPERFONE_SFVOPI_FROM_NUMBER fails the placement via
    the existing retry-policy machinery (record_call_completed), rather than
    attempting an HTTP call with an empty caller ID."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    job_row = {"id": uuid4(), "lead_id": uuid4(), "customer_id": uuid4()}
    att_row = {"id": uuid4()}

    customer_res = MagicMock()
    customer_res.mappings.return_value.one_or_none.return_value = {"phone": "+919999999999"}
    mock_session.execute.side_effect = [customer_res]

    gateway.record_call_completed = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "job": {"id": job_row["id"], "status": "retry_pending"},
            "attempt": {"id": att_row["id"], "status": "completed", "outcome": "technical_failure"},
            "next_status": "retry_pending",
        }
    )

    with patch("app.agent.gateway.settings") as mock_settings:
        mock_settings.SUPERFONE_SFVOPI_FROM_NUMBER = None

        result = await gateway._place_sfvopi_call(tenant_id, job_row, att_row)

    assert result["success"] is False
    assert result["error_code"] == "SFVOPI_MISSING_PHONE_CONFIG"
    gateway.record_call_completed.assert_awaited_once()
    completed_kwargs = gateway.record_call_completed.call_args.kwargs
    assert completed_kwargs["outcome"] == "technical_failure"


@pytest.mark.asyncio
async def test_place_sfvopi_call_fails_cleanly_when_client_raises() -> None:
    """Verify a Superfone client error (e.g. number-not-linked) is caught
    and routed through the same retry-policy machinery, not left to bubble
    as a raw exception."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    job_row = {"id": uuid4(), "lead_id": uuid4(), "customer_id": uuid4()}
    att_row = {"id": uuid4()}

    customer_res = MagicMock()
    customer_res.mappings.return_value.one_or_none.return_value = {"phone": "+919999999999"}
    mock_session.execute.side_effect = [customer_res]

    fake_client = AsyncMock()
    fake_client.initiate_outbound_call = AsyncMock(
        side_effect=ExternalServiceError(
            message="VoIP number is not linked to any SFVoPI app.",
            code="SFVOPI_FROM_NUMBER_NOT_LINKED",
        )
    )

    gateway.record_call_completed = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "job": {"id": job_row["id"], "status": "failed"},
            "attempt": {"id": att_row["id"]},
            "next_status": "failed",
        }
    )

    with (
        patch("app.agent.gateway.get_sfvopi_client", return_value=fake_client),
        patch("app.agent.gateway.settings") as mock_settings,
    ):
        mock_settings.SUPERFONE_SFVOPI_FROM_NUMBER = "+911111111111"
        mock_settings.SUPERFONE_WEBHOOK_SHARED_SECRET.get_secret_value.return_value = "secret"
        mock_settings.APP_PUBLIC_BASE_URL = "https://app.test"

        result = await gateway._place_sfvopi_call(tenant_id, job_row, att_row)

    assert result["success"] is False
    assert result["error_code"] == "SFVOPI_FROM_NUMBER_NOT_LINKED"
    gateway.record_call_completed.assert_awaited_once()
