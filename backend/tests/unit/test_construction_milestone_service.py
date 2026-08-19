"""Unit tests for construction milestone write endpoints: create/progress
business rules, the DB-level duplicate-registration hardening, and the
actual_completion_date/status invariant."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.properties.schemas import ConstructionMilestoneCreate, ConstructionMilestoneUpdate
from app.properties.service import PropertyService


def _service() -> PropertyService:
    session = AsyncMock()
    return PropertyService(session)


@pytest.mark.asyncio
async def test_create_milestone_rejects_unknown_milestone_name() -> None:
    """Verify an unrecognized milestone name is rejected before any DB access."""
    service = _service()
    with pytest.raises(ValidationError) as exc_info:
        await service.create_construction_milestone(
            uuid4(), uuid4(), ConstructionMilestoneCreate(milestone="basement_parking")
        )
    assert exc_info.value.code == "INVALID_MILESTONE"


@pytest.mark.asyncio
async def test_create_milestone_raises_not_found_for_unknown_property() -> None:
    """Verify registering a milestone against a non-existent/cross-tenant property 404s."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.create_construction_milestone(
            uuid4(), uuid4(), ConstructionMilestoneCreate(milestone="foundation")
        )
    assert exc_info.value.code == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_milestone_maps_duplicate_registration_to_conflict() -> None:
    """Verify registering the same milestone twice for a property (violating
    uq_property_construction_milestones_property_stage) maps to a clean
    ConflictError, not a raw IntegrityError."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value={"id": uuid4()})
    service.repository.create_construction_milestone = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                "duplicate key value violates unique constraint "
                '"uq_property_construction_milestones_property_stage"'
            ),
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.create_construction_milestone(
            uuid4(), uuid4(), ConstructionMilestoneCreate(milestone="foundation")
        )
    assert exc_info.value.code == "DUPLICATE_MILESTONE"


@pytest.mark.asyncio
async def test_create_milestone_success() -> None:
    """Verify a fresh milestone registration succeeds and defaults to pending."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value={"id": uuid4()})
    property_id = uuid4()
    milestone_row = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "property_id": property_id,
        "milestone": "foundation",
        "status": "pending",
        "target_date": None,
        "actual_completion_date": None,
        "verified_by": None,
        "notes": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    service.repository.create_construction_milestone = AsyncMock(return_value=milestone_row)

    result = await service.create_construction_milestone(
        uuid4(), property_id, ConstructionMilestoneCreate(milestone="foundation")
    )
    assert result.status == "pending"
    assert result.milestone == "foundation"


@pytest.mark.asyncio
async def test_update_milestone_rejects_unknown_milestone_name() -> None:
    """Verify PATCH on an unrecognized milestone name is rejected before any DB access."""
    service = _service()
    with pytest.raises(ValidationError) as exc_info:
        await service.update_construction_milestone(
            uuid4(), uuid4(), "basement_parking", ConstructionMilestoneUpdate(status="completed")
        )
    assert exc_info.value.code == "INVALID_MILESTONE"


@pytest.mark.asyncio
async def test_update_milestone_never_auto_creates_missing_row() -> None:
    """Verify PATCHing a milestone that was never registered via POST 404s --
    it does not silently create the row (deliberate, documented design choice)."""
    service = _service()
    service.repository.get_construction_milestone_by_stage = AsyncMock(return_value=None)
    create_mock = AsyncMock()
    service.repository.create_construction_milestone = create_mock

    with pytest.raises(NotFoundError) as exc_info:
        await service.update_construction_milestone(
            uuid4(), uuid4(), "foundation", ConstructionMilestoneUpdate(status="in_progress")
        )
    assert exc_info.value.code == "MILESTONE_NOT_FOUND"
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_update_milestone_rejects_completion_date_without_completed_status() -> None:
    """Verify setting actual_completion_date while the (resulting) status is
    not 'completed' is rejected."""
    service = _service()
    service.repository.get_construction_milestone_by_stage = AsyncMock(
        return_value={"id": uuid4(), "status": "in_progress"}
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_construction_milestone(
            uuid4(),
            uuid4(),
            "foundation",
            ConstructionMilestoneUpdate(actual_completion_date=date(2026, 1, 1)),
        )
    assert exc_info.value.code == "ACTUAL_COMPLETION_DATE_REQUIRES_COMPLETED_STATUS"


@pytest.mark.asyncio
async def test_update_milestone_allows_completion_date_with_status_completed_same_request() -> None:
    """Verify setting status='completed' AND actual_completion_date together
    in the same request is allowed (the invariant checks the resulting
    status, not just the pre-existing one)."""
    service = _service()
    milestone_id = uuid4()
    service.repository.get_construction_milestone_by_stage = AsyncMock(
        return_value={"id": milestone_id, "status": "in_progress"}
    )
    service.repository.update_construction_milestone = AsyncMock(
        return_value={
            "id": milestone_id,
            "tenant_id": uuid4(),
            "property_id": uuid4(),
            "milestone": "foundation",
            "status": "completed",
            "target_date": None,
            "actual_completion_date": date(2026, 1, 1),
            "verified_by": None,
            "notes": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    result = await service.update_construction_milestone(
        uuid4(),
        uuid4(),
        "foundation",
        ConstructionMilestoneUpdate(status="completed", actual_completion_date=date(2026, 1, 1)),
    )
    assert result.status == "completed"
    assert result.actual_completion_date == date(2026, 1, 1)


@pytest.mark.asyncio
async def test_update_milestone_allows_completion_date_when_already_completed() -> None:
    """Verify setting actual_completion_date is allowed when the EXISTING
    status is already 'completed' (not being changed in this request)."""
    service = _service()
    milestone_id = uuid4()
    service.repository.get_construction_milestone_by_stage = AsyncMock(
        return_value={"id": milestone_id, "status": "completed"}
    )
    service.repository.update_construction_milestone = AsyncMock(
        return_value={
            "id": milestone_id,
            "tenant_id": uuid4(),
            "property_id": uuid4(),
            "milestone": "foundation",
            "status": "completed",
            "target_date": None,
            "actual_completion_date": date(2026, 1, 2),
            "verified_by": None,
            "notes": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    result = await service.update_construction_milestone(
        uuid4(),
        uuid4(),
        "foundation",
        ConstructionMilestoneUpdate(actual_completion_date=date(2026, 1, 2)),
    )
    assert result.actual_completion_date == date(2026, 1, 2)


@pytest.mark.asyncio
async def test_update_milestone_maps_db_error_via_shared_helper() -> None:
    """Verify a DB error during milestone update (e.g. a malformed verified_by
    FK) maps to a clean ValidationError."""
    service = _service()
    service.repository.get_construction_milestone_by_stage = AsyncMock(
        return_value={"id": uuid4(), "status": "in_progress"}
    )
    service.repository.update_construction_milestone = AsyncMock(
        side_effect=DBAPIError(
            "update",
            {},
            Exception(
                'insert or update on table "property_construction_milestones" violates '
                'foreign key constraint "property_construction_milestones_verified_by_fkey"'
            ),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_construction_milestone(
            uuid4(),
            uuid4(),
            "foundation",
            ConstructionMilestoneUpdate(verified_by=uuid4()),
        )
    assert exc_info.value.code == "INVALID_REFERENCE"
