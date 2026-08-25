"""Unit tests for SalesHandoffService.accept_handoff -- the click-to-call
integration point for sales-agent transfer, verifying the fail-closed
"only mark accepted if the bridge actually succeeded" behavior."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agent.handoff_service import SalesHandoffService
from app.core.exceptions import ConflictError, NotFoundError, ValidationError


def _service() -> SalesHandoffService:
    session = AsyncMock()
    return SalesHandoffService(session)


@pytest.mark.asyncio
async def test_accept_handoff_raises_not_found_for_unknown_handoff() -> None:
    """Verify accepting a non-existent/cross-tenant handoff raises NotFoundError."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.accept_handoff(uuid4(), uuid4(), uuid4())
    assert exc_info.value.code == "SALES_HANDOFF_NOT_FOUND"


@pytest.mark.asyncio
async def test_accept_handoff_rejects_already_accepted() -> None:
    """Verify accepting a handoff that is no longer open raises ConflictError."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "accepted", "customer_phone": "+91"}
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.accept_handoff(uuid4(), uuid4(), uuid4())
    assert exc_info.value.code == "SALES_HANDOFF_NOT_OPEN"


@pytest.mark.asyncio
async def test_accept_handoff_rejects_when_assigned_user_has_no_phone() -> None:
    """Verify a staff member with no registered phone cannot accept (click-to-call
    would be impossible)."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "requested", "customer_phone": "+919999999999"}
    )
    service.repository.get_user_phone = AsyncMock(return_value=None)

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_handoff(uuid4(), uuid4(), uuid4())
    assert exc_info.value.code == "ASSIGNED_USER_NO_PHONE"


@pytest.mark.asyncio
async def test_accept_handoff_rejects_when_customer_has_no_phone() -> None:
    """Verify a customer with no phone on file cannot be bridged."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "requested", "customer_phone": None}
    )
    service.repository.get_user_phone = AsyncMock(return_value="+911111111111")

    with pytest.raises(ValidationError) as exc_info:
        await service.accept_handoff(uuid4(), uuid4(), uuid4())
    assert exc_info.value.code == "CUSTOMER_NO_PHONE"


@pytest.mark.asyncio
async def test_accept_handoff_propagates_superfone_user_not_registered() -> None:
    """Verify Superfone's 404 'User not found' (staff phone not registered
    for Dashboard Calling) propagates as the client's clean NotFoundError,
    and the handoff is NOT marked accepted."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "requested", "customer_phone": "+919999999999"}
    )
    service.repository.get_user_phone = AsyncMock(return_value="+911111111111")
    service.repository.accept_sales_handoff = AsyncMock()

    fake_client = AsyncMock()
    fake_client.click_to_call = AsyncMock(
        side_effect=NotFoundError(
            message="Staff phone not registered.", code="SUPERFONE_USER_NOT_REGISTERED"
        )
    )

    with (
        patch("app.agent.handoff_service.get_superfone_crm_client", return_value=fake_client),
        pytest.raises(NotFoundError) as exc_info,
    ):
        await service.accept_handoff(uuid4(), uuid4(), uuid4())

    assert exc_info.value.code == "SUPERFONE_USER_NOT_REGISTERED"
    service.repository.accept_sales_handoff.assert_not_called()


@pytest.mark.asyncio
async def test_accept_handoff_success_places_bridge_then_marks_accepted() -> None:
    """Verify the happy path: click-to-call fires BEFORE the handoff is
    marked accepted, and the request_uuid is surfaced on the result."""
    service = _service()
    handoff_id = uuid4()
    assigned_user_id = uuid4()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={
            "id": handoff_id,
            "status": "requested",
            "customer_phone": "+919999999999",
        }
    )
    service.repository.get_user_phone = AsyncMock(return_value="+911111111111")
    service.repository.accept_sales_handoff = AsyncMock(
        return_value={"id": handoff_id, "status": "accepted", "assigned_user_id": assigned_user_id}
    )

    fake_client = AsyncMock()
    fake_client.click_to_call = AsyncMock(
        return_value={"notification_id": "n1", "sent_success": True, "request_uuid": "c2c_req_1"}
    )

    with patch("app.agent.handoff_service.get_superfone_crm_client", return_value=fake_client):
        result = await service.accept_handoff(uuid4(), handoff_id, assigned_user_id)

    assert result["status"] == "accepted"
    assert result["click_to_call_request_uuid"] == "c2c_req_1"
    fake_client.click_to_call.assert_awaited_once_with(
        customer_number="+919999999999", user_number="+911111111111", call_action="START"
    )


@pytest.mark.asyncio
async def test_accept_handoff_detects_accept_race_after_successful_bridge() -> None:
    """Verify losing the accept-race after the click-to-call bridge already
    succeeded is surfaced as a distinct, clean ConflictError."""
    service = _service()
    service.repository.get_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "requested", "customer_phone": "+919999999999"}
    )
    service.repository.get_user_phone = AsyncMock(return_value="+911111111111")
    service.repository.accept_sales_handoff = AsyncMock(return_value=None)

    fake_client = AsyncMock()
    fake_client.click_to_call = AsyncMock(
        return_value={"notification_id": "n1", "sent_success": True, "request_uuid": "c2c_req_1"}
    )

    with (
        patch("app.agent.handoff_service.get_superfone_crm_client", return_value=fake_client),
        pytest.raises(ConflictError) as exc_info,
    ):
        await service.accept_handoff(uuid4(), uuid4(), uuid4())

    assert exc_info.value.code == "SALES_HANDOFF_ACCEPT_RACE"


# ---------------------------------------------------------------------------
# Handoff REQUEST path + context bundle (deterministic foundation layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_handoff_delegates_to_repository_with_bundle() -> None:
    """The request path goes through the service so every caller (AI tool,
    future orchestrator) gets the context bundle attached."""
    service = _service()
    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()
    service.repository.create_sales_handoff = AsyncMock(
        return_value={"id": uuid4(), "status": "requested"}
    )

    await service.request_handoff(
        tenant_id, lead_id, customer_id, "Wants senior rep", 3, "note"
    )

    call = service.repository.create_sales_handoff.call_args
    assert call.args[:3] == (tenant_id, lead_id, customer_id)
    # conversation_summary stays None: phase 2's AI summarizer fills it.
    assert call.kwargs["conversation_summary"] is None


@pytest.mark.asyncio
async def test_context_bundle_queries_are_all_tenant_scoped() -> None:
    """Every query feeding the handoff briefing must filter on tenant_id --
    a briefing that leaked another tenant's contact would be a data breach."""
    from unittest.mock import MagicMock

    from app.agent.repository import AgentRepository

    session = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.one_or_none.return_value = None
    res.mappings.return_value.all.return_value = []
    session.execute.return_value = res

    repo = AgentRepository(session)
    tenant_id = uuid4()

    bundle = await repo.build_handoff_context_bundle(tenant_id, uuid4(), uuid4())

    assert session.execute.await_count == 4
    for call in session.execute.await_args_list:
        assert call.args[1]["tenant_id"] == tenant_id

    # Nothing model-generated in this phase.
    assert bundle["context_snapshot"]["generated_by"] == "deterministic"
    assert (
        bundle["context_snapshot"]["conversation_summary_status"]
        == "pending_phase_2_ai_summarizer"
    )
    assert bundle["prior_ai_actions"] is None
