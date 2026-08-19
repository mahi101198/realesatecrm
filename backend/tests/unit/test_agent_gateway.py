"""Unit tests for AgentGateway boundary abstraction, call start DNC recheck, retry policy, and stuck job recovery."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.gateway import AgentGateway


@pytest.mark.asyncio
async def test_agent_gateway_prepare_call_success() -> None:
    """Verify prepare_call builds snapshot context and transitions job queued -> preparing -> ready."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    call_job_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()

    # Mock claim_job_for_update
    gateway.repository.claim_job_for_update = AsyncMock(
        return_value={
            "id": call_job_id,
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "customer_id": customer_id,
            "status": "queued",
        }
    )
    # Mock update_job_status
    gateway.orchestrator.update_job_status = AsyncMock(
        side_effect=[
            {"id": call_job_id, "status": "preparing"},
            {"id": call_job_id, "status": "ready"},
        ]
    )

    # Mock build_pre_call_context
    mock_context = MagicMock()
    mock_context.model_dump.return_value = {"lead_id": str(lead_id), "status": "new"}
    gateway.context_service.build_pre_call_context = AsyncMock(return_value=mock_context)

    # Mock save_agent_call_context_snapshot
    gateway.repository.save_agent_call_context_snapshot = AsyncMock(
        return_value={"id": uuid4()}
    )

    res = await gateway.prepare_call(tenant_id, call_job_id)
    assert res["job"]["status"] == "ready"
    assert "pre_call_context" in res
    gateway.repository.save_agent_call_context_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_agent_gateway_start_call_rechecks_dnc() -> None:
    """Verify start_call rechecks DNC at call start and blocks call if DNC active."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    call_job_id = uuid4()
    customer_id = uuid4()

    gateway.repository.claim_job_for_update = AsyncMock(
        return_value={
            "id": call_job_id,
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "status": "ready",
        }
    )
    # DNC recheck returns True
    gateway.repository.check_do_not_call = AsyncMock(return_value=True)
    gateway.orchestrator.update_job_status = AsyncMock(
        return_value={"id": call_job_id, "status": "cancelled", "last_error_code": "DO_NOT_CALL_ACTIVE"}
    )

    res = await gateway.start_call(tenant_id, call_job_id)
    assert res["success"] is False
    assert res["error_code"] == "DO_NOT_CALL_ACTIVE"
    assert res["job"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_gateway_record_call_completed_retry_policy() -> None:
    """Verify record_call_completed queues retry when outcome is retryable and attempt_count < max_attempts."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    tenant_id = uuid4()
    call_job_id = uuid4()
    attempt_id = uuid4()

    gateway.repository.claim_job_for_update = AsyncMock(
        return_value={
            "id": call_job_id,
            "tenant_id": tenant_id,
            "status": "calling",
            "attempt_count": 1,
            "max_attempts": 3,
        }
    )

    mock_att_res = MagicMock()
    mock_att_res.mappings.return_value.one_or_none.return_value = {
        "id": attempt_id,
        "status": "completed",
        "outcome": "no_answer",
    }
    mock_job_res = MagicMock()
    mock_job_res.mappings.return_value.one.return_value = {
        "id": call_job_id,
        "status": "retry_pending",
        "attempt_count": 1,
    }
    mock_session.execute.side_effect = [mock_att_res, mock_job_res]

    res = await gateway.record_call_completed(
        tenant_id=tenant_id,
        call_job_id=call_job_id,
        call_attempt_id=attempt_id,
        outcome="no_answer",
        duration_seconds=0,
    )
    assert res["next_status"] == "retry_pending"
    assert res["job"]["status"] == "retry_pending"


@pytest.mark.asyncio
async def test_agent_gateway_reconcile_stuck_jobs() -> None:
    """Verify reconcile_stuck_jobs recovers active jobs exceeding timeout threshold."""
    mock_session = AsyncMock()
    gateway = AgentGateway(mock_session)

    gateway.repository.find_and_reconcile_stuck_jobs = AsyncMock(
        return_value=[{"job_id": str(uuid4()), "new_status": "retry_pending"}]
    )

    reconciled = await gateway.reconcile_stuck_jobs(uuid4(), timeout_minutes=15)
    assert len(reconciled) == 1
    assert reconciled[0]["new_status"] == "retry_pending"
