"""Unit tests for Property Booking domain schemas and cancellation business rules."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.bookings.schemas import PropertyBookingCreate, PropertyBookingUpdate
from app.bookings.service import PropertyBookingService
from app.core.exceptions import ConflictError, NotFoundError, ValidationError


def test_property_booking_create_schema() -> None:
    """Verify PropertyBookingCreate schema validates required fields."""
    booking = PropertyBookingCreate(
        property_id=uuid4(), customer_id=uuid4(), booking_amount=Decimal("50000")
    )
    assert booking.booking_amount == Decimal("50000")
    assert booking.lead_id is None


@pytest.mark.asyncio
async def test_update_booking_rejects_direct_converted_status() -> None:
    """Verify PATCH cannot set booking_status='converted' directly (system-only transition)."""
    session = AsyncMock()
    service = PropertyBookingService(session)
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "booking_status": "active"}
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_booking(
            uuid4(), uuid4(), PropertyBookingUpdate(booking_status="converted")
        )
    assert exc_info.value.code == "INVALID_BOOKING_STATUS_TRANSITION"


@pytest.mark.asyncio
async def test_cancel_already_converted_booking_raises_conflict() -> None:
    """Verify cancelling a booking that is no longer 'active' raises ConflictError."""
    session = AsyncMock()
    service = PropertyBookingService(session)
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "booking_status": "converted"}
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.update_booking(
            uuid4(), uuid4(), PropertyBookingUpdate(booking_status="cancelled")
        )
    assert exc_info.value.code == "BOOKING_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_create_booking_validates_property_belongs_to_tenant() -> None:
    """Verify booking creation checks the property exists in this tenant."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    service = PropertyBookingService(session)
    data = PropertyBookingCreate(
        property_id=uuid4(), customer_id=uuid4(), booking_amount=Decimal("50000")
    )

    with pytest.raises(NotFoundError) as exc_info:
        await service.create_booking(uuid4(), uuid4(), data)
    assert exc_info.value.code == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_booking_maps_check_violation_to_validation_error() -> None:
    """Verify a CHECK constraint violation at INSERT time (e.g. a negative
    booking_amount that somehow reached the DB despite Pydantic's ge=0) maps
    to a clean ValidationError, covering the TOCTOU window between the
    pre-checks and the INSERT."""
    session = AsyncMock()
    ok_result = MagicMock()
    ok_result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = ok_result

    service = PropertyBookingService(session)
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'new row for relation "property_bookings" violates check '
                'constraint "chk_property_bookings_amount"'
            ),
        )
    )

    data = PropertyBookingCreate(
        property_id=uuid4(), customer_id=uuid4(), booking_amount=Decimal("50000")
    )
    with pytest.raises(ValidationError) as exc_info:
        await service.create_booking(uuid4(), uuid4(), data)
    assert exc_info.value.code == "CHECK_CONSTRAINT_VIOLATION"


@pytest.mark.asyncio
async def test_update_booking_maps_db_error_to_clean_error() -> None:
    """Verify a DB-level failure during a booking update (e.g. a stale FK
    reference) maps to a clean ValidationError, not a raw 500."""
    session = AsyncMock()
    service = PropertyBookingService(session)
    booking_id = uuid4()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": booking_id, "booking_status": "active"}
    )
    service.repository.update_notes_and_status = AsyncMock(
        side_effect=DBAPIError(
            "update",
            {},
            Exception('null value in column "notes" violates not-null constraint'),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_booking(uuid4(), booking_id, PropertyBookingUpdate(notes="x"))
    assert exc_info.value.code == "MISSING_REQUIRED_FIELD"
