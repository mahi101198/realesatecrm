"""Unit tests for Follow-up domain schemas and repository logic."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.followups.repository import FollowUpRepository
from app.followups.schemas import FollowUpCreate


def test_followup_create_schema() -> None:
    """Verify FollowUpCreate defaults and serialization."""
    lead_id = uuid4()
    future_time = datetime.now(UTC) + timedelta(days=1)
    f_up = FollowUpCreate(
        lead_id=lead_id,
        scheduled_at=future_time,
        reason="Follow up with client",
    )
    assert f_up.lead_id == lead_id
    assert f_up.follow_up_type == "ai_call"
    assert f_up.metadata == {}


@pytest.mark.asyncio
async def test_followup_repository_create_serializes_metadata() -> None:
    """Verify metadata dict is converted to JSON string to prevent asyncpg DataError."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.one.return_value = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "lead_id": uuid4(),
        "customer_id": uuid4(),
        "scheduled_at": datetime.now(UTC),
        "status": "pending",
        "metadata": {},
    }
    session.execute.return_value = mock_result

    repo = FollowUpRepository(session)
    tenant_id = uuid4()
    customer_id = uuid4()
    f_up = FollowUpCreate(
        lead_id=uuid4(),
        scheduled_at=datetime.now(UTC),
        reason="Testing metadata serialization",
        metadata={"source": "test_script", "key": "value"},
    )

    await repo.create(tenant_id=tenant_id, customer_id=customer_id, created_by=None, data=f_up)

    assert session.execute.called
    call_args = session.execute.call_args
    params = call_args[0][1]

    # Crucial check: metadata must be a JSON string, not a python dict
    assert isinstance(params["metadata"], str)
    assert json.loads(params["metadata"]) == {"source": "test_script", "key": "value"}
