"""Unit tests for Location domain schemas and service duplicate-guard logic."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError
from app.locations.schemas import LocationCreate, LocationUpdate
from app.locations.service import LocationService


def test_location_create_schema_defaults() -> None:
    """Verify LocationCreate schema validation and defaults."""
    loc = LocationCreate(name="Gurgaon", city="Gurgaon")
    assert loc.name == "Gurgaon"
    assert loc.country == "India"
    assert loc.is_active is True


def test_location_update_schema_partial() -> None:
    """Verify LocationUpdate supports partial deactivation-only updates."""
    update = LocationUpdate(is_active=False)
    dumped = update.model_dump(exclude_unset=True)
    assert dumped == {"is_active": False}


@pytest.mark.asyncio
async def test_create_location_rejects_case_insensitive_duplicate() -> None:
    """Verify duplicate name+city (case/whitespace-insensitive) raises ConflictError."""
    session = AsyncMock()
    service = LocationService(session)
    service.repository.get_by_normalized_name_city = AsyncMock(
        return_value={"id": uuid4(), "name": "Gurgaon", "city": "Gurgaon"}
    )
    service.repository.create = AsyncMock()

    with pytest.raises(ConflictError) as exc_info:
        await service.create_location(uuid4(), LocationCreate(name="gurgaon ", city="Gurgaon"))

    assert exc_info.value.code == "DUPLICATE_LOCATION"
    service.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_get_location_not_found_raises() -> None:
    """Verify fetching a non-existent location raises NotFoundError."""
    session = AsyncMock()
    service = LocationService(session)
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await service.get_location(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_create_location_survives_db_level_race_not_just_precheck() -> None:
    """Verify the actual DB-level race path is hardened, not just the
    application pre-check: two concurrent requests can both pass the
    case-insensitive pre-check (nothing found yet) and then race on the
    INSERT -- the loser must hit the DB's uq_locations_tenant_name_city
    index and get a clean ConflictError, not a raw IntegrityError."""
    session = AsyncMock()
    service = LocationService(session)
    # Pre-check passes for BOTH concurrent requests (nothing found yet).
    service.repository.get_by_normalized_name_city = AsyncMock(return_value=None)
    # This request loses the race at the DB level.
    service.repository.create = AsyncMock(
        side_effect=IntegrityError(
            "insert",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_locations_tenant_name_city"'
            ),
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.create_location(uuid4(), LocationCreate(name="Gurgaon", city="Gurgaon"))
    assert exc_info.value.code == "DUPLICATE_LOCATION"


@pytest.mark.asyncio
async def test_update_location_maps_db_level_duplicate_to_conflict() -> None:
    """Verify renaming a location into a collision with another existing
    location (only detectable at UPDATE time, not pre-checked) raises a
    clean ConflictError instead of a raw IntegrityError."""
    session = AsyncMock()
    service = LocationService(session)
    location_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": location_id, "name": "Old"})
    service.repository.update = AsyncMock(
        side_effect=IntegrityError(
            "update", {}, Exception("duplicate key value violates unique constraint")
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.update_location(uuid4(), location_id, LocationUpdate(name="Gurgaon"))
    assert exc_info.value.code == "DUPLICATE_LOCATION"
